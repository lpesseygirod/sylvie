import AppKit
import Foundation
import os
import Photos
import WebKit

let photosLog = Logger(subsystem: "app.sylvie.mac", category: "photos")

/// Copies the Photos library's two index databases (Photos.sqlite: photos, dates, faces → people;
/// search/psi.sqlite: places, things, events) into Sylvie's data folder, where the server searches
/// them (lea_profile/photos.py). Lea, 2026-11-08: "show me the photos with…".
///
/// macOS only lets an app with Full Disk Access read inside the library; Lea grants it in System
/// Settings (Sylvie can't, and wouldn't). Without it this does nothing and says so in status.json.
/// Only these index files are copied, never a photo; everything stays on the Mac.
final class PhotosSync {
    private let database = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Pictures/Photos Library.photoslibrary/database")
    private let dest = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Sylvie/app-data/photos")
    private var timer: Timer?
    private let queue = DispatchQueue(label: "app.sylvie.photos-sync", qos: .utility)

    /// (where in the library, name in Sylvie's copy). The -shm files are caches SQLite rebuilds.
    private var files: [(URL, String)] {
        [
            (database.appendingPathComponent("Photos.sqlite"), "Photos.sqlite"),
            (database.appendingPathComponent("Photos.sqlite-wal"), "Photos.sqlite-wal"),
            (database.appendingPathComponent("search/psi.sqlite"), "psi.sqlite"),
            (database.appendingPathComponent("search/psi.sqlite-wal"), "psi.sqlite-wal"),
        ]
    }

    func start() {
        sync()
        // Photos.sqlite is large (867 MB on Lea's Mac, 2026-11-08) and Photos touches it all the time:
        // a copy every 6 hours keeps it fresh enough without wearing the disk.
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 60 * 60, repeats: true) { [weak self] _ in self?.sync() }
    }

    func sync() {
        queue.async { [weak self] in self?.copyIfChanged() }
    }

    private func copyIfChanged() {
        let fm = FileManager.default
        try? fm.createDirectory(at: dest, withIntermediateDirectories: true)
        let main = files[0].0
        // Without Full Disk Access, even opening the file fails (TCC), whatever its permissions say.
        guard let handle = try? FileHandle(forReadingFrom: main) else {
            writeStatus(["access": false])
            return
        }
        try? handle.close()
        let stamps = files.compactMap { (src, _) -> Double? in
            (try? fm.attributesOfItem(atPath: src.path)[.modificationDate] as? Date)?.timeIntervalSince1970
        }
        let version = String(format: "%.0f", stamps.max() ?? 0)
        let marker = dest.appendingPathComponent("copied-at")
        if (try? String(contentsOf: marker, encoding: .utf8)) == version { return }
        let incoming = dest.appendingPathComponent(".incoming")
        try? fm.removeItem(at: incoming)
        do {
            try fm.createDirectory(at: incoming, withIntermediateDirectories: true)
            for (src, name) in files where fm.fileExists(atPath: src.path) {
                try fm.copyItem(at: src, to: incoming.appendingPathComponent(name))
            }
            for (_, name) in files {
                let target = dest.appendingPathComponent(name)
                try? fm.removeItem(at: target)  // Sylvie's old copy, not Lea's library
                try? fm.removeItem(at: dest.appendingPathComponent(name + "-shm"))
                let fresh = incoming.appendingPathComponent(name)
                if fm.fileExists(atPath: fresh.path) { try fm.moveItem(at: fresh, to: target) }
            }
            try version.write(to: marker, atomically: true, encoding: .utf8)
            try? fm.removeItem(at: incoming)
            writeStatus(["access": true, "copied_at": ISO8601DateFormatter().string(from: Date())])
            photosLog.notice("photo library index copied")
        } catch {
            photosLog.notice("photo library copy failed: \(error.localizedDescription, privacy: .public)")
            writeStatus(["access": true, "error": error.localizedDescription])
        }
    }

    private func writeStatus(_ status: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: status) else { return }
        try? data.write(to: dest.appendingPathComponent("status.json"))
    }
}

/// `sylvie-photo://thumb/<UUID>?s=360` in Sylvie's web views: the photo, drawn by Apple's PhotoKit
/// at that size. Photos never leave the Mac and aren't copied; the first one asks Lea for Photos access.
final class PhotoSchemeHandler: NSObject, WKURLSchemeHandler {
    static let shared = PhotoSchemeHandler()
    private var stopped = Set<ObjectIdentifier>()

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return fail(task) }
        let uuid = url.lastPathComponent
        let size = Int(URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "s" })?.value ?? "") ?? 360
        authorized { [weak self] ok in
            guard let self else { return }
            guard ok, let asset = PHAsset.fetchAssets(withLocalIdentifiers: ["\(uuid)/L0/001"], options: nil).firstObject
            else { return self.fail(task) }
            let options = PHImageRequestOptions()
            options.deliveryMode = .highQualityFormat
            options.resizeMode = .fast
            options.isNetworkAccessAllowed = false  // what's on the Mac only
            let side = CGFloat(min(max(size, 64), 2400))
            PHImageManager.default().requestImage(
                for: asset, targetSize: CGSize(width: side, height: side), contentMode: .aspectFit, options: options
            ) { image, _ in
                DispatchQueue.main.async {
                    guard !self.stopped.contains(ObjectIdentifier(task)) else { return }
                    guard let image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                          let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.82])
                    else { return self.fail(task) }
                    task.didReceive(URLResponse(url: url, mimeType: "image/jpeg", expectedContentLength: jpeg.count,
                                                textEncodingName: nil))
                    task.didReceive(jpeg)
                    task.didFinish()
                }
            }
        }
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        stopped.insert(ObjectIdentifier(task))
    }

    private func fail(_ task: WKURLSchemeTask) {
        DispatchQueue.main.async {
            guard !self.stopped.contains(ObjectIdentifier(task)) else { return }
            task.didFailWithError(NSError(domain: "SylviePhoto", code: 404))
        }
    }

    private func authorized(_ then: @escaping (Bool) -> Void) {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        switch status {
        case .authorized, .limited: then(true)
        case .notDetermined:
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { s in then(s == .authorized || s == .limited) }
        default: then(false)
        }
    }
}
