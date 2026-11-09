// swift-tools-version: 6.0
// sylvie-small: Apple's on-device model (Foundation Models, macOS 26) for Sylvie's small background jobs
// (efficiency plan A3). Free, private, memory managed by macOS. Called by core/small_model.py.
import PackageDescription

let package = Package(
    name: "SmallModel",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(
            name: "sylvie-small",
            swiftSettings: [.swiftLanguageMode(.v5)],
            linkerSettings: [.linkedFramework("FoundationModels")]
        ),
    ]
)
