// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "AgatEdgeIOS",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "AgatEdgeCore", targets: ["AgatEdgeCore"]),
    ],
    targets: [
        .target(name: "AgatEdgeCore"),
        .testTarget(name: "AgatEdgeCoreTests", dependencies: ["AgatEdgeCore"]),
    ]
)
