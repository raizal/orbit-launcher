plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

/**
 * versionCode = minutes since 2026-01-01T00:00Z when the build ran, so any newer build (from any
 * branch) installs over an older one — via adb and via Wi-Fi updates (docs/voice-notes.md → App
 * updates). The watch is not debuggable: a lower versionCode is refused even with `adb install -r -d`.
 * A ValueSource so the configuration cache re-reads the clock on every build. Override with
 * `-PorbitVersionCode=<n>` (e.g. for an update test).
 */
abstract class BuildMinutes : ValueSource<Int, ValueSourceParameters.None> {
    override fun obtain(): Int = ((System.currentTimeMillis() - 1_767_225_600_000L) / 60_000L).toInt()
}

val orbitVersionCode: Int = providers.gradleProperty("orbitVersionCode").orNull?.toInt()
    ?: providers.of(BuildMinutes::class.java) {}.get()
val orbitCommit: String = runCatching {
    providers.exec { commandLine("git", "rev-parse", "--short=7", "HEAD"); isIgnoreExitValue = true }
        .standardOutput.asText.get().trim()
}.getOrNull()?.takeIf { it.isNotEmpty() } ?: "nogit"

android {
    namespace = "com.orbit"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.orbit"
        minSdk = 27
        targetSdk = 34
        versionCode = orbitVersionCode
        versionName = "0.1.0-$orbitCommit"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            // Debug-signed so release/R8 builds can be installed on the watch for perf checks.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}

dependencies {
    implementation(project(":core"))
    implementation(project(":feature-voice"))
    implementation(project(":feature-launcher"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.foundation)
}
