import SwiftUI

@main
struct SylvieApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuContent(controller: delegate.controller)
        } label: {
            MenuIcon(controller: delegate.controller)
        }
        .menuBarExtraStyle(.menu)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    @MainActor let controller = AppController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        MainActor.assumeIsolated { controller.start() }
    }
}

struct MenuIcon: View {
    @ObservedObject var controller: AppController
    var body: some View {
        Image(nsImage: controller.menuIcon)
            .accessibilityLabel("Sylvie: \(controller.state.label)")
    }
}

struct MenuContent: View {
    @ObservedObject var controller: AppController

    var body: some View {
        Text(controller.state.label)
        if !controller.detail.isEmpty {
            Text(controller.detail)
        }
        Divider()
        Button("Open chat") { controller.openChat() }
        Button("Ask Sylvie…") { controller.showPanel() }
            .keyboardShortcut(" ", modifiers: .option)
        Button("Voice mode") { controller.openVoiceMode() }
        if controller.state == .speaking || controller.state == .thinking {
            Button("Stop") { controller.interrupt() }
        }
        Divider()
        Button(controller.state == .muted ? "Unmute mic" : "Mute mic") { controller.toggleMute() }
        Button(controller.learningPaused ? "Resume learning" : "Pause learning") { controller.toggleLearningPause() }
        Button("Dashboard") { controller.openDashboard() }
        Toggle("Launch at login", isOn: Binding(
            get: { controller.launchAtLogin },
            set: { controller.setLaunchAtLogin($0) }
        ))
        Divider()
        Button("Quit Sylvie") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}
