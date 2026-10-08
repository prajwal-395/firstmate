import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation

private let resolveBundleID = "com.blackmagic-design.DaVinciResolve"
private let panelWidth: CGFloat = 420
private let panelHeight: CGFloat = 92

private struct Lease: Decodable {
    let owner: String
    let purpose: String
    let since: Double
}

private struct Activity {
    let jobID: String
    let purpose: String
    let owner: String
    let started: Double
    let cancellable: Bool
    let additionalJobs: Int
}

private final class CatchView: NSView {
    var title = "Resolve is in use by Ren"
    var detail = ""
    var elapsed = ""
    var message = "Clicks in Resolve are shielded while Ren holds the lease."
    var cancelEnabled = false
    var cancelAction: (() -> Void)?
    var takeoverAction: (() -> Void)?
    private let titleLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(labelWithString: "")
    private let elapsedLabel = NSTextField(labelWithString: "")
    private let messageLabel = NSTextField(labelWithString: "")
    private let stop = NSButton(title: "Stop safely", target: nil, action: nil)
    private let takeover = NSButton(title: "Take over", target: nil, action: nil)

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.withAlphaComponent(0.08).cgColor
        let card = NSVisualEffectView(frame: NSRect(x: 0, y: 0,
                                                    width: panelWidth,
                                                    height: panelHeight))
        card.material = .hudWindow
        card.blendingMode = .withinWindow
        card.state = .active
        card.wantsLayer = true
        card.layer?.cornerRadius = 13
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor.white.withAlphaComponent(0.24).cgColor
        addSubview(card)

        titleLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        titleLabel.textColor = .white
        detailLabel.font = .systemFont(ofSize: 11, weight: .medium)
        detailLabel.textColor = NSColor.white.withAlphaComponent(0.86)
        elapsedLabel.font = .monospacedDigitSystemFont(ofSize: 11, weight: .medium)
        elapsedLabel.textColor = NSColor.white.withAlphaComponent(0.86)
        messageLabel.font = .systemFont(ofSize: 10, weight: .regular)
        messageLabel.textColor = NSColor.white.withAlphaComponent(0.76)
        for label in [titleLabel, detailLabel, elapsedLabel, messageLabel] {
            label.lineBreakMode = .byTruncatingTail
            card.addSubview(label)
        }
        titleLabel.frame = NSRect(x: 14, y: 68, width: 250, height: 18)
        detailLabel.frame = NSRect(x: 14, y: 49, width: 260, height: 16)
        elapsedLabel.frame = NSRect(x: 278, y: 68, width: 118, height: 18)
        // Keep the status message in its own column. The takeover button starts
        // at x=278, so a full-card label would draw underneath it.
        messageLabel.frame = NSRect(x: 14, y: 12, width: 250, height: 16)

        stop.bezelStyle = .rounded
        stop.font = .systemFont(ofSize: 11, weight: .semibold)
        stop.contentTintColor = .white
        stop.target = self
        stop.action = #selector(stopPressed)
        card.addSubview(stop)
        stop.frame = NSRect(x: 278, y: 44, width: 118, height: 24)

        takeover.bezelStyle = .rounded
        takeover.font = .systemFont(ofSize: 11, weight: .semibold)
        takeover.contentTintColor = .systemOrange
        takeover.target = self
        takeover.action = #selector(takeoverPressed)
        card.addSubview(takeover)
        takeover.frame = NSRect(x: 278, y: 12, width: 118, height: 24)
        updateLabels()
    }

    required init?(coder: NSCoder) { nil }

    func update(title: String, detail: String, elapsed: String,
                message: String, canStop: Bool) {
        self.title = title
        self.detail = detail
        self.elapsed = elapsed
        self.message = message
        self.cancelEnabled = canStop
        updateLabels()
    }

    private func updateLabels() {
        titleLabel.stringValue = title
        detailLabel.stringValue = detail
        elapsedLabel.stringValue = elapsed
        messageLabel.stringValue = message
        stop.isEnabled = cancelEnabled
        stop.toolTip = cancelEnabled
            ? "Request a cooperative stop. The shield stays until the broker releases the lease."
            : "This job has no safe running stop path. The shield stays until the job finishes."
        takeover.toolTip = stop.toolTip
    }

    @objc private func stopPressed() { cancelAction?() }
    @objc private func takeoverPressed() { takeoverAction?() }

    override func hitTest(_ point: NSPoint) -> NSView? {
        // Buttons retain their normal hit targets. Every other point in the
        // Resolve window is consumed by this transparent shield.
        for child in subviews.reversed() {
            let local = convert(point, to: child)
            if let hit = child.hitTest(local) { return hit }
        }
        return self
    }

    override func draw(_ dirtyRect: NSRect) {
        // The content itself is transparent; the small HUD card carries the
        // only visible treatment while this view catches Resolve clicks.
    }
}

private final class OverlayController: NSObject, NSApplicationDelegate {
    private let lockDir: URL
    private let leaseURL: URL
    private let socketURL: URL
    private let singletonFD: Int32
    private var panel: NSPanel?
    private var content: CatchView?
    private var timer: Timer?
    private var lastActivity: Activity?
    private var requestedStop = false

    init(lockDir: URL) {
        self.lockDir = lockDir
        leaseURL = lockDir.appendingPathComponent("resolve_instance.lease.json")
        socketURL = lockDir.appendingPathComponent("ren-resolved.sock")
        let fd = open(lockDir.appendingPathComponent("resolve-activity-overlay.lock").path,
                      O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        singletonFD = fd
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard singletonFD >= 0, flock(singletonFD, LOCK_EX | LOCK_NB) == 0 else {
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func refresh() {
        guard let activity = currentActivity() else {
            panel?.orderOut(nil)
            panel = nil
            content = nil
            lastActivity = nil
            requestedStop = false
            NSApp.terminate(nil)
            return
        }
        lastActivity = activity
        guard let target = resolveWindow(), target.frontmost else {
            panel?.orderOut(nil)
            return
        }
        let frame = target.frame
        let panelFrame = NSRect(x: frame.minX + 16,
                                y: frame.maxY - panelHeight - 16,
                                width: panelWidth,
                                height: panelHeight)
        if panel == nil { createPanel() }
        panel?.setFrame(panelFrame, display: true)
        if let number = target.number {
            panel?.order(.above, relativeTo: Int(number))
        } else {
            panel?.orderFrontRegardless()
        }
        let canStop = activity.cancellable
        let elapsed = max(0, Int(Date().timeIntervalSince1970 - activity.started))
        let extra = activity.additionalJobs > 0
            ? " +\(activity.additionalJobs) queued/running" : ""
        let detail = "\(activity.purpose) · \(activity.owner)\(extra)"
        let message: String
        if requestedStop {
            message = "Stop requested. Resolve stays shielded until the safe point releases the lease."
        } else if canStop {
            message = "Cancellable qualification probe · no project or timeline edits"
        } else {
            message = "This job has no safe running Stop path. Resolve clicks are shielded until it finishes."
        }
        content?.update(title: "Ren is using DaVinci Resolve",
                        detail: detail,
                        elapsed: String(format: "Elapsed %02d:%02d", elapsed / 60, elapsed % 60),
                        message: message,
                        canStop: canStop && !requestedStop)
        content?.cancelAction = { [weak self] in self?.sendStop(jobID: activity.jobID) }
        content?.takeoverAction = { [weak self] in self?.sendStop(jobID: activity.jobID) }
    }

    private func createPanel() {
        let window = NSPanel(contentRect: NSRect(x: 0, y: 0,
                                                 width: panelWidth,
                                                 height: panelHeight),
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        window.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.normalWindow)))
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.ignoresMouseEvents = false
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        window.isReleasedWhenClosed = false
        window.contentView = CatchView(frame: NSRect(x: 0, y: 0,
                                                     width: panelWidth,
                                                     height: panelHeight))
        panel = window
        content = window.contentView as? CatchView
    }

    private func readLease() -> Lease? {
        guard let data = try? Data(contentsOf: leaseURL) else { return nil }
        return try? JSONDecoder().decode(Lease.self, from: data)
    }

    private func currentActivity() -> Activity? {
        guard let lease = readLease() else { return nil }
        let parts = lease.purpose.split(separator: " ")
        if parts.count >= 3, parts[0] == "ren-resolved",
           let id = parts.last.map(String.init),
           let reply = brokerRequest(["op": "status", "id": id]),
           let job = reply["job"] as? [String: Any],
           (job["state"] as? String) == "running",
           let kind = job["kind"] as? String {
            let params = job["params"] as? [String: Any] ?? [:]
            let purpose = (params["purpose"] as? String) ?? kind
            return Activity(
                jobID: id, purpose: purpose,
                owner: (job["owner"] as? String) ?? lease.owner,
                started: (job["started"] as? Double)
                    ?? (job["submitted"] as? Double)
                    ?? lease.since,
                cancellable: kind == "resolve.activity_probe",
                additionalJobs: 0)
        }
        return Activity(
            jobID: parts.last.map(String.init) ?? "",
            purpose: lease.purpose,
            owner: lease.owner,
            started: lease.since,
            cancellable: false,
            additionalJobs: 0)
    }

    private struct ResolveWindow {
        let frame: NSRect
        let number: Int32?
        let frontmost: Bool
    }

    private func resolveWindow() -> ResolveWindow? {
        guard let app = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == resolveBundleID
        }) else { return nil }
        let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                   kCGNullWindowID) as? [[String: Any]]) ?? []
        let matches = windows.compactMap { info -> (NSRect, Int32)? in
            guard (info[kCGWindowOwnerPID as String] as? Int) == Int(app.processIdentifier),
                  (info[kCGWindowLayer as String] as? Int) == 0,
                  let bounds = info[kCGWindowBounds as String] as? [String: CGFloat],
                  let x = bounds["X"], let y = bounds["Y"],
                  let width = bounds["Width"], let height = bounds["Height"],
                  width > 300, height > 200,
                  let number = info[kCGWindowNumber as String] as? Int32 else { return nil }
            let screenHeight = NSScreen.screens.first?.frame.maxY ?? 0
            return (NSRect(x: x, y: screenHeight - y - height,
                           width: width, height: height), number)
        }
        guard let largest = matches.max(by: {
            $0.0.width * $0.0.height < $1.0.width * $1.0.height
        }) else { return nil }
        return ResolveWindow(frame: largest.0, number: largest.1,
                             frontmost: NSWorkspace.shared.frontmostApplication?.processIdentifier
                                == app.processIdentifier)
    }

    private func sendStop(jobID: String) {
        guard !jobID.isEmpty else { return }
        guard let result = brokerRequest(["op": "cancel", "id": jobID]) else {
            updateMessage("The broker did not accept the Stop request. The shield remains active.")
            return
        }
        if let error = result["error"] as? String {
            updateMessage(error)
        } else {
            requestedStop = true
        }
    }

    private func updateMessage(_ message: String) {
        content?.update(title: "Ren is using DaVinci Resolve",
                        detail: lastActivity?.purpose ?? "",
                        elapsed: "",
                        message: message,
                        canStop: false)
    }

    private func brokerRequest(_ request: [String: Any]) -> [String: Any]? {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let path = socketURL.path
        let copied = path.withCString({ cstr in
            strncpy(&address.sun_path.0, cstr, MemoryLayout.size(ofValue: address.sun_path) - 1)
        })
        guard copied != nil else { return nil }
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { return nil }
        guard let encoded = try? JSONSerialization.data(withJSONObject: request) else { return nil }
        var body = encoded
        body.append(0x0A)
        _ = body.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, $0.count) }
        var response = [UInt8](repeating: 0, count: 4096)
        let count = Darwin.read(fd, &response, response.count)
        guard count > 0 else { return nil }
        let data = Data(response.prefix(count))
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}

let arguments = CommandLine.arguments
let envLockDir = ProcessInfo.processInfo.environment["PIPELINE_RESOLVE_LOCK_DIR"]
let lockPath = arguments.dropFirst().first ?? envLockDir ?? NSTemporaryDirectory()
private let controller = OverlayController(lockDir: URL(fileURLWithPath: lockPath, isDirectory: true))
let application = NSApplication.shared
application.delegate = controller
application.run()
