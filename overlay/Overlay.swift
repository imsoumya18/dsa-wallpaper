// dsa-overlay — shows out/overlay.png in a click-through window that sits
// above the wallpaper (including video wallpapers such as WallSpace) but
// below desktop icons and widgets. Transparent pixels show what's behind.
//
// It also writes out/screen.json with the main display's real pixel size,
// Retina scale and menu bar / Dock insets, so the renderer can lay tiles out
// for this exact screen.
//
// Build: swiftc -O -swift-version 5 Overlay.swift -o dsa-overlay
// Run:   dsa-overlay <overlay.png> <screen.json> [levelOffset]

import AppKit

final class OverlayController: NSObject {
    let imageURL: URL
    let screenURL: URL
    let levelOffset: Int
    var windows: [NSWindow] = []
    var lastModified: Date?

    init(imageURL: URL, screenURL: URL, levelOffset: Int) {
        self.imageURL = imageURL
        self.screenURL = screenURL
        self.levelOffset = levelOffset
    }

    func start() {
        NotificationCenter.default.addObserver(
            self, selector: #selector(screensChanged(_:)),
            name: NSApplication.didChangeScreenParametersNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(screensChanged(_:)),
            name: NSWorkspace.didWakeNotification, object: nil)
        Timer.scheduledTimer(timeInterval: 1.0, target: self, selector: #selector(tick),
                             userInfo: nil, repeats: true)
        rebuild()
    }

    @objc func screensChanged(_ note: Notification) { rebuild() }

    @objc func tick() { reloadIfChanged() }

    func rebuild() {
        writeScreenInfo()
        windows.forEach { $0.orderOut(nil) }
        let level = Int(CGWindowLevelForKey(.desktopIconWindow)) + levelOffset
        windows = NSScreen.screens.map { (screen: NSScreen) -> NSWindow in
            let w = NSWindow(contentRect: screen.frame, styleMask: .borderless,
                             backing: .buffered, defer: false)
            w.setFrame(screen.frame, display: false)
            w.isOpaque = false
            w.backgroundColor = .clear
            w.hasShadow = false
            w.ignoresMouseEvents = true
            w.isReleasedWhenClosed = false
            w.level = NSWindow.Level(rawValue: level)
            w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
            let view = NSImageView(frame: NSRect(origin: .zero, size: screen.frame.size))
            view.imageScaling = .scaleAxesIndependently
            view.autoresizingMask = [.width, .height]
            w.contentView = view
            w.orderFrontRegardless()
            return w
        }
        lastModified = nil
        reloadIfChanged()
    }

    func reloadIfChanged() {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: imageURL.path),
              let modified = attrs[.modificationDate] as? Date,
              modified != lastModified,
              let data = try? Data(contentsOf: imageURL),
              let image = NSImage(data: data) else { return }
        lastModified = modified
        for w in windows {
            (w.contentView as? NSImageView)?.image = image
        }
    }

    /// Main display geometry in pixels, for the renderer.
    func writeScreenInfo() {
        guard let s = NSScreen.screens.first else { return }
        let f = s.frame, v = s.visibleFrame, k = s.backingScaleFactor
        let info: [String: Double] = [
            "width": Double(f.width * k),
            "height": Double(f.height * k),
            "scale": Double(k),
            "inset_top": Double((f.maxY - v.maxY) * k),
            "inset_bottom": Double((v.minY - f.minY) * k),
            "inset_left": Double((v.minX - f.minX) * k),
            "inset_right": Double((f.maxX - v.maxX) * k),
        ]
        guard let data = try? JSONSerialization.data(
            withJSONObject: info, options: [.prettyPrinted, .sortedKeys]) else { return }
        // only touch the file when something changed — the renderer watches it
        if (try? Data(contentsOf: screenURL)) != data {
            try? data.write(to: screenURL, options: .atomic)
        }
    }
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: dsa-overlay <overlay.png> <screen.json> [levelOffset]\n".data(using: .utf8)!)
    exit(2)
}
let offset = args.count > 3 ? (Int(args[3]) ?? -1) : -1

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // no Dock icon, no menu bar
let controller = OverlayController(
    imageURL: URL(fileURLWithPath: args[1]),
    screenURL: URL(fileURLWithPath: args[2]),
    levelOffset: offset)
controller.start()
app.run()
