import com.google.protobuf.gradle.*

plugins {
    id("com.android.library")
    id("com.google.protobuf")
}

// The checkout is verified, then copied/patched under build/. Never modify upstream.
val engineSource = rootProject.file(
    providers.gradleProperty("ppssppSourceDir").orElse("third_party/ppsspp").get()
)
val pythonCommand = providers.gradleProperty("pythonCommand").orElse("python3")
val generatedJava = layout.buildDirectory.dir("generated/ppsspp-java")
val engineAbis = providers.gradleProperty("dockAbis")
    .orElse("arm64-v8a,armeabi-v7a").get().split(',').map(String::trim)
require(engineAbis.isNotEmpty() && engineAbis.all {
    it in setOf("arm64-v8a", "armeabi-v7a", "x86_64")
}) { "dockAbis must be a comma-separated selection of arm64-v8a,armeabi-v7a,x86_64" }

val verifyPpssppSource by tasks.registering(Exec::class) {
    group = "verification"
    description = "Verify the exact PPSSPP source and pinned recursive dependencies."
    commandLine(pythonCommand.get(), rootProject.file("scripts/prepare_engine.py"),
        "--source", engineSource, "--verify-only")
}

val preparePpssppJava by tasks.registering(Exec::class) {
    dependsOn(verifyPpssppSource)
    inputs.dir(engineSource.resolve("android/src/org"))
    inputs.file(rootProject.file("scripts/patch_engine_java.py"))
    outputs.dir(generatedJava)
    commandLine(pythonCommand.get(), rootProject.file("scripts/patch_engine_java.py"),
        "--source", engineSource, "--output", generatedJava.get().asFile)
}

android {
    // JNI entry points and protobuf Java classes retain their upstream packages.
    namespace = "org.ppsspp.ppsspp"
    compileSdk = 36
    ndkVersion = "29.0.14206865"

    defaultConfig {
        minSdk = 26
        buildConfigField("String", "FLAVOR", "\"dock\"")
        ndk { abiFilters.addAll(engineAbis) }
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DANDROID=true", "-DANDROID_PLATFORM=android-26",
                    "-DANDROID_TOOLCHAIN=clang", "-DANDROID_CPP_FEATURES=",
                    "-DANDROID_STL=c++_shared", "-DHEADLESS=OFF", "-DUNITTEST=OFF",
                    "-DATLAS_TOOL=OFF", "-DUSE_DISCORD=OFF"
                )
                targets += "ppsspp_jni"
            }
        }
    }
    buildTypes {
        // The installable debug APK still runs an optimized emulator, with symbols.
        debug {
            isMinifyEnabled = false
            externalNativeBuild.cmake.arguments += "-DCMAKE_BUILD_TYPE=RelWithDebInfo"
        }
        release {
            isMinifyEnabled = false
            externalNativeBuild.cmake.arguments += "-DCMAKE_BUILD_TYPE=Release"
        }
    }
    externalNativeBuild {
        cmake {
            path = engineSource.resolve("CMakeLists.txt")
            version = "3.22.1"
        }
    }
    buildFeatures {
        buildConfig = true
        aidl = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    sourceSets {
        getByName("main") {
            java.srcDir(generatedJava)
            res.srcDirs(engineSource.resolve("android/res"),
                engineSource.resolve("android/src/main/res"))
            assets.srcDir(engineSource.resolve("assets"))
            proto { srcDir(engineSource.resolve("android/src/main/proto")) }
        }
    }
    lint {
        // Keep upstream's existing, version-pinned compatibility suppressions.
        baseline = engineSource.resolve("android/lint-baseline.xml")
        abortOnError = true
    }
}

tasks.named("preBuild") { dependsOn(preparePpssppJava) }
// Native model/configuration tasks can run before Android's preBuild.
tasks.configureEach {
    if (name.startsWith("configureCMake") || name.startsWith("buildCMake")) {
        dependsOn(verifyPpssppSource)
    }
}

protobuf {
    protoc { artifact = "com.google.protobuf:protoc:3.25.3" }
    generateProtoTasks {
        all().configureEach { builtins { maybeCreate("java").option("lite") } }
    }
}

dependencies {
    implementation(project(":session-contract"))
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.documentfile:documentfile:1.1.0")
    implementation("com.google.protobuf:protobuf-javalite:4.33.5")
}
