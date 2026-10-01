// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MekenCore",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "MekenCore", targets: ["MekenCore"])],
    targets: [
        .target(name: "MekenCore"),
        .testTarget(name: "MekenCoreTests", dependencies: ["MekenCore"])
    ],
    swiftLanguageModes: [.v6]
)

