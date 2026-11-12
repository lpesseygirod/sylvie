import AppKit
import WebKit

/// Shared web setup: both the floating panel and the full chat window show Sylvie's own web UI
/// (served by the local server), signed in with the Keychain token as a cookie.
enum WebSetup {
    static func configuration(handler: WKScriptMessageHandler) -> WKWebViewConfiguration {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.add(handler, name: "sylvie")
        config.setURLSchemeHandler(PhotoSchemeHandler.shared, forURLScheme: "sylvie-photo")  // Lea's photos
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

    static func json(_ value: Any) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]),
              let s = String(data: data, encoding: .utf8) else { return "null" }
        return s
    }

    static let background = NSColor(srgbRed: 0x0E / 255, green: 0x0F / 255, blue: 0x13 / 255, alpha: 1)
}

/// confirm(), alert() and prompt() from the web UI (deleting a chat, editing a fact) as real dialogs.
/// Without this, WKWebView silently answers "no" to every confirm().
final class WebDialogs: NSObject, WKUIDelegate {
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        completionHandler(a.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert()
        a.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.window.initialFirstResponder = field
        completionHandler(a.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }
}

/// A Spotlight-like floating panel (design/DESIGN.md §2.3): mini orb + live transcript, the answer,
/// confirmation cards. Dark translucent material with blur, radius 22, soft shadow.
final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

final class PanelController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    var onOpenChat: ((String?) -> Void)?
    private(set) var panel: FloatingPanel!
    private(set) var hasPendingConfirmation = false
    private var webView: WKWebView!
    private let dialogs = WebDialogs()
    private let base: URL
    private var loaded = false
    private var pending: [String] = []
    private var outsideClicks: Any?
    private let width: CGFloat = 620

    init(base: URL) {
        self.base = base
        super.init()
        panel = FloatingPanel(
            contentRect: NSRect(x: 0, y: 0, width: width, height: 132),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: true
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true

        let glass = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: width, height: 132))
        glass.material = .hudWindow
        glass.blendingMode = .behindWindow
        glass.state = .active
        glass.appearance = NSAppearance(named: .darkAqua)
        glass.wantsLayer = true
        glass.layer?.cornerRadius = 22
        glass.layer?.masksToBounds = true
        glass.layer?.borderWidth = 1
        glass.layer?.borderColor = NSColor(srgbRed: 0x2E / 255, green: 0x32 / 255, blue: 0x3D / 255, alpha: 1).cgColor
        // A dark tint over the blur, like the design's rgba(18,19,24,.94) but letting a little through.
        let tint = NSView(frame: glass.bounds)
        tint.wantsLayer = true
        tint.layer?.backgroundColor = NSColor(srgbRed: 18 / 255, green: 19 / 255, blue: 24 / 255, alpha: 0.82).cgColor
        tint.autoresizingMask = [.width, .height]
        glass.addSubview(tint)

        webView = WKWebView(frame: glass.bounds, configuration: WebSetup.configuration(handler: self))
        webView.navigationDelegate = self
        webView.uiDelegate = dialogs
        webView.setValue(false, forKey: "drawsBackground")
        webView.autoresizingMask = [.width, .height]
        glass.addSubview(webView)
        panel.contentView = glass
    }

    func load() {
        loaded = false
        webView.load(URLRequest(url: URL(string: "/?embed=panel", relativeTo: base)!))
    }

    var isVisible: Bool { panel.isVisible }
    var isKey: Bool { panel.isKeyWindow }

    func show() {
        if !panel.isVisible, let screen = NSScreen.main {
            let f = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: f.midX - panel.frame.width / 2, y: f.maxY - f.height * 0.16))
        }
        panel.orderFrontRegardless()
        watchOutsideClicks()
    }

    func focusForTyping() {
        NSApp.activate()
        panel.makeKeyAndOrderFront(nil)
        run("window.sylvie && window.sylvie.focusInput()")
    }

    func hide() {
        panel.orderOut(nil)
        if let m = outsideClicks { NSEvent.removeMonitor(m) }
        outsideClicks = nil
    }

    /// A click anywhere outside the panel closes it, unless a "Send?" card is waiting.
    private func watchOutsideClicks() {
        guard outsideClicks == nil else { return }
        outsideClicks = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.panel.isVisible, !self.hasPendingConfirmation else { return }
                self.hide()
            }
        }
    }

    /// Forwards one server event to the web UI, which renders it.
    func send(event: [String: Any]) {
        run("window.sylvie && window.sylvie.voiceEvent(\(WebSetup.json(event)))")
    }

    func voiceState(_ state: String, detail: String = "") {
        run("window.sylvie && window.sylvie.voiceState(\(WebSetup.json(state)), \(WebSetup.json(detail)))")
    }

    func voiceLevel(_ level: Double) {
        guard loaded, panel.isVisible else { return }
        webView.evaluateJavaScript("window.sylvie && window.sylvie.voiceLevel(\(level))")
    }

    private func run(_ js: String) {
        if loaded { webView.evaluateJavaScript(js) } else { pending.append(js) }
    }

    /// The panel grows with its content (up to a limit), keeping its top edge where it is.
    private func resize(toContentHeight h: CGFloat) {
        let height = min(max(h, 96), 560)
        guard abs(panel.frame.height - height) > 1 else { return }
        var f = panel.frame
        f.origin.y += f.height - height
        f.size.height = height
        panel.setFrame(f, display: true, animate: false)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
        pending.forEach { webView.evaluateJavaScript($0) }
        pending.removeAll()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "openChat":
            hide()
            onOpenChat?(body["conversation_id"] as? String)
        case "closePanel":
            if !hasPendingConfirmation { hide() }
        case "panelSize":
            if let h = body["height"] as? Double { resize(toContentHeight: CGFloat(h)) }
        case "pendingConfirm":
            hasPendingConfirmation = body["value"] as? Bool ?? false
        default:
            break
        }
    }
}

/// The full chat window, like other AI chat apps. Also hosts voice mode (/voice), whose microphone and
/// speaker are the app's own (wake word, echo cancellation, Kokoro): the page sends button presses
/// here and the app sends it states and events.
final class ChatWindowController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    /// Messages from the page (voiceMode, voiceListen, voiceStop, voiceMute) for the app controller.
    var onMessage: (([String: Any]) -> Void)?
    private var window: NSWindow?
    private var webView: WKWebView?
    private let dialogs = WebDialogs()
    private let base: URL

    init(base: URL) { self.base = base }

    var isVisible: Bool { window?.isVisible ?? false }

    func open(conversationId: String?) {
        var path = "/"
        if let cid = conversationId { path += "?c=\(cid)" }
        open(path: path)
    }

    func open(path: String) {
        if window == nil {
            let w = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                backing: .buffered, defer: false
            )
            w.title = "Sylvie"
            w.titlebarAppearsTransparent = true
            w.titleVisibility = .hidden
            w.backgroundColor = WebSetup.background
            w.appearance = NSAppearance(named: .darkAqua)
            w.isReleasedWhenClosed = false
            w.minSize = NSSize(width: 420, height: 520)
            w.center()
            w.setFrameAutosaveName("SylvieChat")
            let wv = WKWebView(frame: w.contentLayoutRect, configuration: WebSetup.configuration(handler: self))
            wv.uiDelegate = dialogs
            wv.navigationDelegate = self
            wv.setValue(false, forKey: "drawsBackground")
            w.contentView = wv
            window = w
            webView = wv
        }
        webView?.load(URLRequest(url: URL(string: path, relativeTo: base)!))
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
    }

    // MARK: Voice mode bridge

    func voiceState(_ state: String, detail: String = "") {
        webView?.evaluateJavaScript("window.sylvieVoice && window.sylvieVoice.state(\(WebSetup.json(state)), \(WebSetup.json(detail)))")
    }

    func voiceEvent(_ event: [String: Any]) {
        webView?.evaluateJavaScript("window.sylvieVoice && window.sylvieVoice.event(\(WebSetup.json(event)))")
    }

    func voiceLevel(_ level: Double) {
        webView?.evaluateJavaScript("window.sylvieVoice && window.sylvieVoice.level(\(level))")
    }

    // MARK: Messages

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        if type == "print" { printDocument() } else { onMessage?(body) }
    }

    /// Leaving the voice page (link, reload, closed window) ends voice mode for the app.
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        onMessage?(["type": "voiceMode", "active": false])
    }

    /// "Export PDF" in the work panel: the print sheet, where macOS offers "Save as PDF".
    private func printDocument() {
        guard let webView, let window else { return }
        let info = NSPrintInfo.shared
        info.topMargin = 36
        info.bottomMargin = 36
        let op = webView.printOperation(with: info)
        op.view?.frame = webView.bounds
        op.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }
}
