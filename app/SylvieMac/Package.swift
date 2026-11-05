// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SylvieMac",
    platforms: [.macOS("26.0")],
    products: [
        .executable(name: "Sylvie", targets: ["Sylvie"]),
    ],
    targets: [
        // Pure logic, unit-tested without microphone or UI.
        .target(name: "SylvieCore", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(
            name: "Sylvie",
            dependencies: ["SylvieCore"],
            swiftSettings: [.swiftLanguageMode(.v5)],
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("Speech"),
                .linkedFramework("WebKit"),
                .linkedFramework("Carbon"),
                .linkedFramework("ServiceManagement"),
            ]
        ),
        .testTarget(name: "SylvieCoreTests", dependencies: ["SylvieCore"], swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
