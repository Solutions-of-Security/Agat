#include <jni.h>
#include <android/log.h>
#include <dlfcn.h>
#include <vulkan/vulkan.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#if AGAT_WITH_LLAMA
#include "ggml-backend.h"
#include "llama.h"
#endif

namespace {

void throw_java(JNIEnv * env, const char * message) {
    jclass type = env->FindClass("java/lang/IllegalStateException");
    if (type != nullptr) env->ThrowNew(type, message);
}

#if AGAT_WITH_LLAMA
std::string utf8(JNIEnv * env, jstring value) {
    if (value == nullptr) throw std::invalid_argument("String argument is null");
    const char * raw = env->GetStringUTFChars(value, nullptr);
    if (raw == nullptr) throw std::runtime_error("Unable to read UTF-8 string");
    std::string result(raw);
    env->ReleaseStringUTFChars(value, raw);
    return result;
}
#endif

bool vulkan_available() {
    VkApplicationInfo application{};
    application.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    application.pApplicationName = "AGAT Edge";
    application.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo info{};
    info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    info.pApplicationInfo = &application;
    VkInstance instance = VK_NULL_HANDLE;
    if (vkCreateInstance(&info, nullptr, &instance) != VK_SUCCESS) return false;
    uint32_t count = 0;
    const VkResult result = vkEnumeratePhysicalDevices(instance, &count, nullptr);
    vkDestroyInstance(instance, nullptr);
    return result == VK_SUCCESS && count > 0;
}

#if AGAT_WITH_LLAMA
struct Engine {
    llama_model * model = nullptr;
    uint32_t context_tokens = 4096;
    std::mutex mutex;
    std::atomic_bool abort_requested{false};

    ~Engine() {
        if (model != nullptr) llama_model_free(model);
    }
};

std::once_flag backend_once;

std::unique_ptr<Engine> create_engine(const std::string & path, int backend, int context_tokens) {
    std::call_once(backend_once, [] { ggml_backend_load_all(); });
    auto engine = std::make_unique<Engine>();
    engine->context_tokens = static_cast<uint32_t>(std::clamp(context_tokens, 512, 32768));
    llama_model_params parameters = llama_model_default_params();
    parameters.n_gpu_layers = backend == 0 ? -1 : 0; // Kotlin VULKAN ordinal is 0.
    engine->model = llama_model_load_from_file(path.c_str(), parameters);
    if (engine->model == nullptr) throw std::runtime_error("Unable to load pinned GGUF model");
    return engine;
}

std::string complete(Engine & engine, const std::string & prompt, int max_tokens) {
    std::scoped_lock lock(engine.mutex);
    engine.abort_requested.store(false, std::memory_order_release);
    const llama_vocab * vocab = llama_model_get_vocab(engine.model);
    const int token_count = -llama_tokenize(vocab, prompt.c_str(), prompt.size(), nullptr, 0, true, true);
    if (token_count <= 0) throw std::runtime_error("Unable to tokenize prompt");
    if (static_cast<uint32_t>(token_count + max_tokens) > engine.context_tokens) {
        throw std::runtime_error("Prompt exceeds configured context window");
    }
    std::vector<llama_token> tokens(static_cast<size_t>(token_count));
    if (llama_tokenize(vocab, prompt.c_str(), prompt.size(), tokens.data(), tokens.size(), true, true) < 0) {
        throw std::runtime_error("Unable to tokenize prompt");
    }

    llama_context_params context_parameters = llama_context_default_params();
    context_parameters.n_ctx = engine.context_tokens;
    context_parameters.n_batch = std::min<uint32_t>(engine.context_tokens, 512);
    context_parameters.n_threads = std::max(1, static_cast<int>(std::thread::hardware_concurrency()) - 1);
    context_parameters.n_threads_batch = context_parameters.n_threads;
    llama_context * raw_context = llama_init_from_model(engine.model, context_parameters);
    if (raw_context == nullptr) throw std::runtime_error("Unable to create llama context");
    std::unique_ptr<llama_context, decltype(&llama_free)> context(raw_context, llama_free);
    llama_set_abort_callback(context.get(), [](void * data) {
        return static_cast<std::atomic_bool *>(data)->load(std::memory_order_acquire);
    }, &engine.abort_requested);

    llama_sampler_chain_params sampler_parameters = llama_sampler_chain_default_params();
    llama_sampler * raw_sampler = llama_sampler_chain_init(sampler_parameters);
    if (raw_sampler == nullptr) throw std::runtime_error("Unable to create llama sampler");
    std::unique_ptr<llama_sampler, decltype(&llama_sampler_free)> sampler(raw_sampler, llama_sampler_free);
    llama_sampler_chain_add(sampler.get(), llama_sampler_init_greedy());

    llama_batch batch = llama_batch_get_one(tokens.data(), static_cast<int32_t>(tokens.size()));
    if (llama_model_has_encoder(engine.model)) {
        if (llama_encode(context.get(), batch) != 0) throw std::runtime_error("llama_encode failed");
        llama_token start = llama_model_decoder_start_token(engine.model);
        if (start == LLAMA_TOKEN_NULL) start = llama_vocab_bos(vocab);
        batch = llama_batch_get_one(&start, 1);
    }

    std::string output;
    output.reserve(static_cast<size_t>(max_tokens) * 6);
    for (int generated = 0; generated < max_tokens; ++generated) {
        if (engine.abort_requested.load(std::memory_order_acquire)) {
            throw std::runtime_error("Inference cancelled by control plane");
        }
        if (llama_decode(context.get(), batch) != 0) {
            if (engine.abort_requested.load(std::memory_order_acquire)) {
                throw std::runtime_error("Inference cancelled by control plane");
            }
            throw std::runtime_error("llama_decode failed");
        }
        llama_token next = llama_sampler_sample(sampler.get(), context.get(), -1);
        if (llama_vocab_is_eog(vocab, next)) break;
        std::vector<char> piece(256);
        int size = llama_token_to_piece(vocab, next, piece.data(), piece.size(), 0, true);
        if (size < 0) {
            piece.resize(static_cast<size_t>(-size));
            size = llama_token_to_piece(vocab, next, piece.data(), piece.size(), 0, true);
        }
        if (size < 0) throw std::runtime_error("Unable to decode output token");
        output.append(piece.data(), static_cast<size_t>(size));
        batch = llama_batch_get_one(&next, 1);
    }
    return output;
}
#endif

} // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_io_agat_edge_NativeLlama_nativeCreate(JNIEnv * env, jobject, jstring path, jint backend, jint context_tokens) {
    try {
#if AGAT_WITH_LLAMA
        return reinterpret_cast<jlong>(create_engine(utf8(env, path), backend, context_tokens).release());
#else
        (void) path; (void) backend; (void) context_tokens;
        throw std::runtime_error("llama.cpp runtime is not packaged; set agatLlamaCppDir to pinned b10686 sources");
#endif
    } catch (const std::exception & error) {
        throw_java(env, error.what());
        return 0;
    }
}

extern "C" JNIEXPORT jstring JNICALL
Java_io_agat_edge_NativeLlama_nativeComplete(JNIEnv * env, jobject, jlong handle, jstring prompt, jint max_tokens) {
    try {
#if AGAT_WITH_LLAMA
        auto * engine = reinterpret_cast<Engine *>(handle);
        if (engine == nullptr) throw std::runtime_error("Inference engine handle is invalid");
        const std::string result = complete(*engine, utf8(env, prompt), std::clamp(static_cast<int>(max_tokens), 1, 512));
        return env->NewStringUTF(result.c_str());
#else
        (void) handle; (void) prompt; (void) max_tokens;
        throw std::runtime_error("llama.cpp runtime is not packaged");
#endif
    } catch (const std::exception & error) {
        throw_java(env, error.what());
        return nullptr;
    }
}

extern "C" JNIEXPORT void JNICALL
Java_io_agat_edge_NativeLlama_nativeCancel(JNIEnv *, jobject, jlong handle) {
#if AGAT_WITH_LLAMA
    auto * engine = reinterpret_cast<Engine *>(handle);
    if (engine != nullptr) engine->abort_requested.store(true, std::memory_order_release);
#else
    (void) handle;
#endif
}

extern "C" JNIEXPORT void JNICALL
Java_io_agat_edge_NativeLlama_nativeDestroy(JNIEnv *, jobject, jlong handle) {
#if AGAT_WITH_LLAMA
    delete reinterpret_cast<Engine *>(handle);
#else
    (void) handle;
#endif
}

extern "C" JNIEXPORT jboolean JNICALL
Java_io_agat_edge_NativeLlama_00024Companion_nativeHasVulkan(JNIEnv *, jobject) {
    return vulkan_available() ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jlong JNICALL
Java_io_agat_edge_NativeLlama_00024Companion_nativeNnapiFeatureLevel(JNIEnv *, jobject) {
    void * library = dlopen("libneuralnetworks.so", RTLD_NOW | RTLD_LOCAL);
    if (library == nullptr) return 0;
    using FeatureLevel = int64_t (*)();
    auto function = reinterpret_cast<FeatureLevel>(dlsym(library, "ANeuralNetworks_getRuntimeFeatureLevel"));
    const int64_t level = function == nullptr ? 0 : function();
    dlclose(library);
    return static_cast<jlong>(level);
}
