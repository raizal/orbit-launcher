plugins {
    alias(libs.plugins.android.library)
}

android {
    namespace = "com.orbit.launcher"
    compileSdk = 37
    defaultConfig { minSdk = 27 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
