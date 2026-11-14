plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.orbit.voice"
    compileSdk = 37
    defaultConfig { minSdk = 27 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}

dependencies {
    implementation(project(":core"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.androidx.work)
    implementation(libs.okhttp)
    implementation(libs.zxing.embedded)
    implementation(libs.zxing.core)
    testImplementation(libs.junit)
}
