plugins {
    id("com.android.application")
    kotlin("android")
    kotlin("plugin.serialization")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Production endpoint is deliberately fixed; local overrides apply to Debug only.
val apiBaseUrl = "https://194.238.43.134"
val debugApiBaseUrl = providers.gradleProperty("mekenDebugApiBaseUrl").orElse(apiBaseUrl).get().trimEnd('/')
val keystorePath = providers.environmentVariable("MEKEN_ANDROID_KEYSTORE_FILE").orNull
val keystorePassword = providers.environmentVariable("MEKEN_ANDROID_KEYSTORE_PASSWORD").orNull
val signingAlias = providers.environmentVariable("MEKEN_ANDROID_KEY_ALIAS").orNull
val signingKeyPassword = providers.environmentVariable("MEKEN_ANDROID_KEY_PASSWORD").orNull
val hasSigning = listOf(keystorePath, keystorePassword, signingAlias, signingKeyPassword).all { !it.isNullOrBlank() }

android {
    namespace = "kz.unknown.meken"
    compileSdk = 36
    defaultConfig {
        applicationId = "kz.unknown.meken"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        // JSON quoting preserves backslashes/quotes in a generated Java string.
        buildConfigField("String", "MEKEN_API_BASE_URL", groovy.json.JsonOutput.toJson(apiBaseUrl))
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    if (hasSigning) {
        signingConfigs.create("production") {
            storeFile = file(keystorePath!!)
            storePassword = keystorePassword
            keyAlias = signingAlias
            keyPassword = signingKeyPassword
        }
    }
    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            buildConfigField("String", "MEKEN_API_BASE_URL", groovy.json.JsonOutput.toJson(debugApiBaseUrl))
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (hasSigning) signingConfig = signingConfigs.getByName("production")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    testOptions { unitTests.isReturnDefaultValues = true }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}
kotlin { jvmToolchain(17) }

val validateReleaseEndpoint by tasks.registering {
    doLast {
        val requested = providers.gradleProperty("mekenApiBaseUrl").orNull
        check(requested == null || requested == apiBaseUrl) {
            "Release API is fixed at https://194.238.43.134; use mekenDebugApiBaseUrl for local Debug builds"
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach { dependsOn(validateReleaseEndpoint) }

dependencies {
    implementation(project(":core"))
    implementation(platform("androidx.compose:compose-bom:2026.06.01"))
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-savedstate:2.10.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("com.squareup.okhttp3:okhttp:5.4.0")
    implementation("io.coil-kt:coil-compose:2.7.0")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    testImplementation("com.squareup.okhttp3:mockwebserver:5.4.0")
    testImplementation("com.squareup.okhttp3:okhttp-tls:5.4.0")
    androidTestImplementation(platform("androidx.compose:compose-bom:2026.06.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
