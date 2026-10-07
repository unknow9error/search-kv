plugins {
    kotlin("jvm")
    kotlin("plugin.serialization")
}
kotlin { jvmToolchain(17) }
dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    testImplementation(kotlin("test-junit"))
    testImplementation("junit:junit:4.13.2")
}
