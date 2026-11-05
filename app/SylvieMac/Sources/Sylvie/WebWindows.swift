import AppKit
import WebKit

/// Shared web setup: both the floating panel and the full chat window show Sylvie's own web UI
/// (served by the local server), signed in with the Keychain token as a cookie.
enum WebSetup {
    static func configuration(handler: WKScriptMessageHandler) -> WKWebViewConfiguration {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.add(handler, name: "sylvie")
        return config
    }

    static func setSessionCookie(token: String, port: Int, then: @escaping () -> Void) {
        let props: [HTTPCookiePropertyKey: Any] = [
            .name: "sylvie_session", .value: token, .domain: "127.0.0.1", .path: "/",
            .expires: Date().addingTimeInterval(60 * 60 * 24 * 365),
        ]
        guard let cookie = HTTPCookie(properties: props) else { return then() }
        WKWebsiteDataStore.default().httpCookieStore.setCookie(cookie, completionHandler: then)
    }
}

/// A Spotlight-like floating panel: live transcript and Sylvie's written answer.
final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class PanelController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    var onOpenChat: ((String?) -> Void)?
    private(set) var panel: FloatingPanel!
    private var webView: WKWebView!
    private let base: URL
    private var loaded = false
    private var pending: [String] = []

    init(base: URL) {
        self.base = base
        super.init()
        panel = FloatingPanel(
            contentRect: NSRect(x: 0, y: 0, width: 640, height: 420),
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView, .nonactivatingPanel],
            backing: .buffered, defer: true
        )
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        webView = WKWebView(frame: panel.contentLayoutRect, configuration: WebSetup.configuration(handler: self))
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        panel.contentView = webView
    }

    func load() {
        loaded = false
        webView.load(URLRequest(url: URL(string: "/?embed=panel", relativeTo: base)!))
    }

    var isVisible: Bool { panel.isVisible }

    func show() {
        if let screen = NSScreen.main {
            let f = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: f.midX - panel.frame.width / 2, y: f.maxY - f.height * 0.18))
        }
        panel.orderFrontRegardless()
    }

    func focusForTyping() {
        NSApp.activate()
        panel.makeKeyAndOrderFront(nil)
        run("document.getElementById('input')?.focus()")
    }

    func hide() { panel.orderOut(nil) }

    /// Forwards one server event to the web UI, which renders it.
    func send(event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event),
              let json = String(data: data, encoding: .utf8) else { return }
        run("window.sylvie && window.sylvie.voiceEvent(\(json))")
    }

    private func run(_ js: String) {
        if loaded { webView.evaluateJavaScript(js) } else { pending.append(js) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
        pending.forEach { webView.evaluateJavaScript($0) }
        pending.removeAll()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        if type == "openChat" { onOpenChat?(body["conversation_id"] as? String) }
        if type == "closePanel" { hide() }
    }
}

/// The full chat window, like Claude or ChatGPT.
final class ChatWindowController: NSObject, WKScriptMessageHandler {
    private var window: NSWindow?
    private var webView: WKWebView?
    private let base: URL

    init(base: URL) { self.base = base }

    func open(conversationId: String?) {
        if window == nil {
            let w = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false
            )
            w.title = "Sylvie"
            w.titlebarAppearsTransparent = true
            w.isReleasedWhenClosed = false
            w.center()
            w.setFrameAutosaveName("SylvieChat")
            let wv = WKWebView(frame: w.contentLayoutRect, configuration: WebSetup.configuration(handler: self))
            w.contentView = wv
            window = w
            webView = wv
        }
        var path = "/"
        if let cid = conversationId { path += "?c=\(cid)" }
        webView?.load(URLRequest(url: URL(string: path, relativeTo: base)!))
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {}
}
