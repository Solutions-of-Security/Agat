import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val llamaCppDir = providers.gradleProperty("agatLlamaCppDir").orElse("").get()
val enableVulkan = providers.gradleProperty("agatEnableVulkan").orElse("true").get().toBoolean()
val cloudProjectNumber = providers.gradleProperty("agatCloudProjectNumber").orElse("0").get()
val spirvHeadersDir = providers.gradleProperty("agatSpirvHeadersDir").orElse("").get()
val glslcExecutable = providers.gradleProperty("agatGlslcExecutable").orElse("").get()
val vulkanHeadersDir = providers.gradleProperty("agatVulkanHeadersDir").orElse("").get()
val expectedLlamaCppCommit = "3173a56471c1753650cd806694145ffd6dcace67"

if (llamaCppDir.isNotBlank()) {
    val sourceTree = file(llamaCppDir)
    require(sourceTree.isDirectory) { "agatLlamaCppDir должен указывать на llama.cpp source tree" }
    val actualCommit = providers.exec {
        commandLine("git", "-C", sourceTree.absolutePath, "rev-parse", "HEAD")
    }.standardOutput.asText.get().trim()
    require(actualCommit == expectedLlamaCppCommit) {
        "llama.cpp должен быть pinned на b10686 / $expectedLlamaCppCommit, получен $actualCommit"
    }
    val dirtyFiles = providers.exec {
        commandLine("git", "-C", sourceTree.absolutePath, "status", "--porcelain", "--untracked-files=all")
    }.standardOutput.asText.get().trim()
    require(dirtyFiles.isEmpty()) { "Pinned llama.cpp source tree содержит незакоммиченные изменения" }
}

android {
    namespace = "io.agat.edge"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.agat.edge"
        minSdk = 28
        targetSdk = 36
        versionCode = 170
        versionName = "1.7.0"

        buildConfigField("long", "PLAY_INTEGRITY_CLOUD_PROJECT", "${cloudProjectNumber}L")
        buildConfigField("boolean", "LLAMA_RUNTIME_PACKAGED", (llamaCppDir.isNotBlank()).toString())
        externalNativeBuild {
            cmake {
                cppFlags += listOf("-std=c++17", "-fexceptions", "-frtti")
                arguments += listOf(
                    "-DAGAT_LLAMA_CPP_DIR=$llamaCppDir",
                    "-DAGAT_ENABLE_VULKAN=${if (enableVulkan) "ON" else "OFF"}",
                )
                if (spirvHeadersDir.isNotBlank()) {
                    arguments += "-DSPIRV-Headers_DIR=$spirvHeadersDir"
                }
                if (glslcExecutable.isNotBlank()) {
                    arguments += "-DVulkan_GLSLC_EXECUTABLE=$glslcExecutable"
                }
                if (vulkanHeadersDir.isNotBlank()) {
                    arguments += "-DVulkan_INCLUDE_DIR=$vulkanHeadersDir"
                }
            }
        }
        ndk { abiFilters += "arm64-v8a" }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    buildFeatures { buildConfig = true }
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.11.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.lifecycle:lifecycle-service:2.9.3")
    implementation("com.google.android.play:integrity:1.5.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.10.2")

    testImplementation("junit:junit:4.13.2")
}
