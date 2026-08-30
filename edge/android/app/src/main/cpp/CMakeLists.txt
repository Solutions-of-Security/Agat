cmake_minimum_required(VERSION 3.22.1)
project(agat_edge_android LANGUAGES C CXX)

add_library(agat_llama SHARED llama_bridge.cpp)
target_compile_features(agat_llama PRIVATE cxx_std_17)
target_compile_options(agat_llama PRIVATE -Wall -Wextra -Werror=return-type)
target_link_libraries(agat_llama PRIVATE android log vulkan dl)
message(STATUS "AGAT Vulkan backend requested: ${AGAT_ENABLE_VULKAN}")

if(AGAT_LLAMA_CPP_DIR AND EXISTS "${AGAT_LLAMA_CPP_DIR}/CMakeLists.txt")
    set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
    set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
    if(AGAT_ENABLE_VULKAN)
        if(ANDROID AND NOT Vulkan_GLSLC_EXECUTABLE)
            find_program(
                AGAT_HOST_GLSLC
                NAMES glslc glslc.exe
                NO_CMAKE_FIND_ROOT_PATH
            )
            if(NOT AGAT_HOST_GLSLC)
                message(FATAL_ERROR "A current host glslc is required; set agatGlslcExecutable to the Vulkan SDK binary")
            endif()
            set(Vulkan_GLSLC_EXECUTABLE "${AGAT_HOST_GLSLC}" CACHE FILEPATH "Vulkan shader compiler" FORCE)
        endif()
        set(GGML_VULKAN ON CACHE BOOL "" FORCE)

        # llama.cpp cross-compiles the runtime but builds its Vulkan shader
        # generator for the host. Make Gradle's bundled Ninja discoverable by
        # that nested build instead of requiring a global Ninja installation.
        get_filename_component(AGAT_HOST_BUILD_TOOL_DIR "${CMAKE_MAKE_PROGRAM}" DIRECTORY)
        cmake_path(
            CONVERT "${AGAT_HOST_BUILD_TOOL_DIR};$ENV{PATH}"
            TO_NATIVE_PATH_LIST AGAT_HOST_BUILD_PATH
            NORMALIZE
        )
        set_property(
            GLOBAL PROPERTY RULE_LAUNCH_CUSTOM
            "${CMAKE_COMMAND} -E env \"PATH=${AGAT_HOST_BUILD_PATH}\""
        )
    endif()
    add_subdirectory("${AGAT_LLAMA_CPP_DIR}" llama.cpp-build EXCLUDE_FROM_ALL)
    if(AGAT_ENABLE_VULKAN AND TARGET ggml-vulkan)
        if(TARGET SPIRV-Headers::SPIRV-Headers)
            target_link_libraries(ggml-vulkan PRIVATE SPIRV-Headers::SPIRV-Headers)
        elseif(SPIRV-Headers_DIR)
            # find_package() runs in llama.cpp's subdirectory, so its imported
            # target is not visible in this parent scope on older CMake.
            get_filename_component(
                AGAT_SPIRV_HEADERS_PREFIX
                "${SPIRV-Headers_DIR}/../../.."
                ABSOLUTE
            )
            if(NOT EXISTS "${AGAT_SPIRV_HEADERS_PREFIX}/include/spirv/unified1/spirv.hpp")
                message(FATAL_ERROR "SPIRV-Headers include tree is incomplete")
            endif()
            target_include_directories(ggml-vulkan PRIVATE "${AGAT_SPIRV_HEADERS_PREFIX}/include")
        endif()
    endif()
    target_compile_definitions(agat_llama PRIVATE AGAT_WITH_LLAMA=1)
    target_link_libraries(agat_llama PRIVATE llama)
else()
    message(WARNING "AGAT llama.cpp runtime is a fail-closed stub. Set agatLlamaCppDir to signed b10686 / 3173a56 sources for inference builds.")
endif()
