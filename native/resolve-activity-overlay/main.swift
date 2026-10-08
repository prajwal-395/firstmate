import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation

private let resolveBundleID = "com.blackmagic-design.DaVinciResolve"
private let panelWidth: CGFloat = 420
private let panelHeight: CGFloat = 160

private struct Lease: Decodable {
    let owner: String
    let purpose: String
    let since: Double
    let pid: Int32
}

private struct Activity {
    let jobID: String
    let purpose: String
    let owner: String
    let started: Double
    let cancellable: Bool
    let additionalJobs: Int
    let description: String
    let mode: String
}

private final class CatchView: NSView {
    var title = "Resolve is in use by Ren"
    var detail = ""
    var elapsed = ""
    var message = "Clicks in Resolve are shielded while Ren holds the lease."
    var cancelEnabled = false
    var shieldEnabled = true
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
        layer?.backgroundColor = NSColor.black.withAlphaComponent(0.035).cgColor
        let card = NSVisualEffectView(frame: cardFrame(in: bounds))
        card.autoresizingMask = [.minXMargin, .minYMargin]
        card.material = .hudWindow
        card.blendingMode = .withinWindow
        card.state = .active
        card.wantsLayer = true
        card.layer?.cornerRadius = 13
        card.layer?.borderWidth = 1
        card.layer?.borderColor = NSColor.white.withAlphaComponent(0.24).cgColor
        addSubview(card)

        titleLabel.font = .systemFont(ofSize: 12, weight: .semibold)
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
        titleLabel.lineBreakMode = .byWordWrapping
        messageLabel.lineBreakMode = .byWordWrapping
        stop.bezelStyle = .rounded
        stop.font = .systemFont(ofSize: 11, weight: .semibold)
        stop.contentTintColor = .white
        stop.target = self
        stop.action = #selector(stopPressed)
        card.addSubview(stop)

        takeover.bezelStyle = .rounded
        takeover.font = .systemFont(ofSize: 11, weight: .semibold)
        takeover.contentTintColor = .systemOrange
        takeover.target = self
        takeover.action = #selector(takeoverPressed)
        card.addSubview(takeover)
        layoutCard(card)
        updateLabels()
    }

    required init?(coder: NSCoder) { nil }

    func update(title: String, detail: String, elapsed: String,
                message: String, canStop: Bool, shieldEnabled: Bool) {
        self.title = title
        self.detail = detail
        self.elapsed = elapsed
        self.message = message
        self.cancelEnabled = canStop
        self.shieldEnabled = shieldEnabled
        layer?.backgroundColor = NSColor.black.withAlphaComponent(
            shieldEnabled ? 0.035 : 0).cgColor
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
        let local = convert(point, to: subviews[0])
        if shieldEnabled {
            if let hit = subviews[0].hitTest(local) { return hit }
            return self
        }
        // Shared/read-only jobs and Take over keep only the buttons live.
        for button in [takeover, stop] {
            let buttonPoint = subviews[0].convert(local, to: button)
            if let hit = button.hitTest(buttonPoint) { return hit }
        }
        return nil
    }

    private func cardFrame(in bounds: NSRect) -> NSRect {
        if bounds.width <= panelWidth && bounds.height <= panelHeight {
            return bounds
        }
        let width = min(panelWidth, max(0, bounds.width - 24))
        let height = min(panelHeight, max(0, bounds.height - 24))
        return NSRect(x: 12, y: max(12, bounds.height - height - 12),
                      width: width, height: height)
    }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        if let card = subviews.first {
            card.frame = cardFrame(in: bounds)
            layoutCard(card)
        }
    }

    private func layoutCard(_ card: NSView) {
        let width = card.bounds.width
        let textWidth = max(80, width - 28)
        titleLabel.frame = NSRect(x: 14, y: 104,
                                  width: textWidth, height: 34)
        detailLabel.frame = NSRect(x: 14, y: 94,
                                   width: max(90, width - 128), height: 16)
        elapsedLabel.frame = NSRect(x: width - 128, y: 94,
                                    width: 114, height: 18)
        messageLabel.frame = NSRect(x: 14, y: 54, width: textWidth, height: 32)
        let buttonWidth = max(64, (width - 42) / 2)
        stop.frame = NSRect(x: 14, y: 18, width: buttonWidth, height: 26)
        takeover.frame = NSRect(x: 28 + buttonWidth, y: 18,
                                width: buttonWidth, height: 26)
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
    private var shieldEnabled = true
    private var takeoverJobID: String?
    private var eventTap: CFMachPort?
    private var eventTapSource: CFRunLoopSource?
    private var inputEventTapAttempted = false
    private var brokerUnavailableSince: Date?

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
        shieldEnabled = activity.mode == "exclusive"
            && takeoverJobID != activity.jobID
        let inputEventTapReady = setInputEventTap(enabled: shieldEnabled)
        let panelFrame = shieldEnabled ? frame : compactFrame(in: frame)
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
        let detail = activity.purpose + extra
        let message: String
        if requestedStop {
            message = "Stop requested. Resolve stays shielded until the safe point releases the lease."
        } else if activity.mode == "shared" {
            message = "Read-only Ren job. Resolve input remains available."
        } else if shieldEnabled && !inputEventTapReady {
            message = "Mouse shield active. macOS has not allowed keyboard shielding."
        } else if canStop {
            message = "Cancellable qualification probe · no project or timeline edits"
        } else {
            message = "This job has no safe running Stop path. Resolve clicks are shielded until it finishes."
        }
        content?.update(title: activity.description,
                        detail: detail,
                        elapsed: String(format: "Elapsed %02d:%02d", elapsed / 60, elapsed % 60),
                        message: message,
                        canStop: canStop && !requestedStop,
                        shieldEnabled: shieldEnabled)
        content?.cancelAction = { [weak self] in self?.sendStop(jobID: activity.jobID) }
        content?.takeoverAction = { [weak self] in self?.takeOver() }
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
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isReleasedWhenClosed = false
        window.contentView = CatchView(frame: NSRect(x: 0, y: 0,
                                                     width: panelWidth,
                                                     height: panelHeight))
        panel = window
        content = window.contentView as? CatchView
    }

    private func compactFrame(in target: NSRect) -> NSRect {
        let width = min(panelWidth, target.width)
        let height = min(panelHeight, target.height)
        let x = min(target.maxX - width, target.minX + 12)
        let y = max(target.minY, target.maxY - height - 12)
        return NSRect(x: x, y: y, width: width, height: height)
    }

    private func readLease() -> Lease? {
        guard let data = try? Data(contentsOf: leaseURL) else { return nil }
        return try? JSONDecoder().decode(Lease.self, from: data)
    }

    private func currentActivity() -> Activity? {
        guard let lease = readLease() else { return currentSharedActivity() }
        guard processIsAlive(lease.pid) else { return nil }
        let parts = lease.purpose.split(separator: " ")
        if parts.count >= 3, parts[0] == "ren-resolved",
           let id = parts.last.map(String.init) {
            if let reply = brokerRequest(["op": "status", "id": id]) {
                brokerUnavailableSince = nil
                if let job = reply["job"] as? [String: Any],
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
                        additionalJobs: 0,
                        description: {
                            let description: String = (job["description"] as? String)
                                ?? (params["description"] as? String) ?? purpose
                            let progress: String = (job["progress"] as? String) ?? ""
                            return progress.isEmpty ? description
                                : "\(description) - \(progress)"
                        }(),
                        mode: (job["mode"] as? String) ?? "exclusive")
                }
            } else {
                if brokerUnavailableSince == nil {
                    brokerUnavailableSince = Date()
                }
                if let unavailableSince = brokerUnavailableSince,
                   Date().timeIntervalSince(unavailableSince) >= 5 {
                    return nil
                }
            }
        }
        // The lease file is the authority for an active exclusive holder.
        // Broker status is useful for the friendly job description, but a
        // transient socket/read failure must not make the shield exit while
        // the lease is still held. The broker clears this file on release.
        return Activity(
            jobID: parts.last.map(String.init) ?? "",
            purpose: lease.purpose,
            owner: lease.owner,
            started: lease.since,
            cancellable: false,
            additionalJobs: 0,
            description: lease.purpose,
            mode: "exclusive")
    }

    private func processIsAlive(_ pid: Int32) -> Bool {
        if kill(pid, 0) == 0 { return true }
        return errno == EPERM
    }

    private func currentSharedActivity() -> Activity? {
        guard let reply = brokerRequest(["op": "activity"]),
              let jobs = reply["jobs"] as? [[String: Any]],
              let job = jobs.first(where: {
                  ($0["mode"] as? String) == "shared"
                      && ($0["state"] as? String) == "running"
              }), let jobID = job["id"] as? String else { return nil }
        let params = job["params"] as? [String: Any] ?? [:]
        let purpose = (params["purpose"] as? String) ?? (job["kind"] as? String) ?? "read"
        let description = (job["description"] as? String) ?? purpose
        let progress = (job["progress"] as? String) ?? ""
        return Activity(
            jobID: jobID, purpose: purpose,
            owner: (job["owner"] as? String) ?? "Ren",
            started: (job["started"] as? Double)
                ?? (job["submitted"] as? Double) ?? Date().timeIntervalSince1970,
            cancellable: false,
            additionalJobs: max(0, (reply["count"] as? Int ?? jobs.count) - 1),
            description: progress.isEmpty ? description
                : "\(description) - \(progress)",
            mode: "shared")
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
            let mainScreen = NSScreen.screens.first(where: {
                ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")]
                    as? NSNumber)?.uint32Value == CGMainDisplayID()
            })
            let screenTop = mainScreen?.frame.maxY
                ?? NSScreen.screens.first?.frame.maxY ?? 0
            return (NSRect(x: x, y: screenTop - y - height,
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
        content?.update(title: lastActivity?.description ?? "Ren is using Resolve",
                        detail: lastActivity?.purpose ?? "",
                        elapsed: "",
                        message: message,
                        canStop: false, shieldEnabled: shieldEnabled)
    }

    private func takeOver() {
        takeoverJobID = lastActivity?.jobID
        shieldEnabled = false
        _ = setInputEventTap(enabled: false)
        if let target = resolveWindow() {
            panel?.setFrame(compactFrame(in: target.frame), display: true)
        }
        content?.update(title: lastActivity?.description ?? "Ren is using Resolve",
                        detail: lastActivity?.purpose ?? "",
                        elapsed: "",
                        message: "Takeover active. Resolve input is enabled.",
                        canStop: false, shieldEnabled: false)
    }

    @discardableResult
    private func setInputEventTap(enabled: Bool) -> Bool {
        shieldEnabled = enabled
        if enabled {
            if eventTap == nil {
                guard !inputEventTapAttempted else { return false }
                inputEventTapAttempted = true
                let mask = (1 << CGEventType.keyDown.rawValue)
                    | (1 << CGEventType.keyUp.rawValue)
                    | (1 << CGEventType.flagsChanged.rawValue)
                    | (1 << CGEventType.leftMouseDown.rawValue)
                    | (1 << CGEventType.leftMouseUp.rawValue)
                    | (1 << CGEventType.rightMouseDown.rawValue)
                    | (1 << CGEventType.rightMouseUp.rawValue)
                    | (1 << CGEventType.otherMouseDown.rawValue)
                    | (1 << CGEventType.otherMouseUp.rawValue)
                    | (1 << CGEventType.scrollWheel.rawValue)
                let context = Unmanaged.passUnretained(self).toOpaque()
                eventTap = CGEvent.tapCreate(
                    tap: .cgSessionEventTap, place: .headInsertEventTap,
                    options: .defaultTap, eventsOfInterest: CGEventMask(mask),
                    callback: OverlayController.keyEventTapCallback,
                    userInfo: context)
                guard let eventTap else { return false }
                eventTapSource = CFMachPortCreateRunLoopSource(
                    kCFAllocatorDefault, eventTap, 0)
                if let eventTapSource {
                    CFRunLoopAddSource(CFRunLoopGetMain(), eventTapSource,
                                       .commonModes)
                    CGEvent.tapEnable(tap: eventTap, enable: true)
                }
            } else if let eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
            }
            return eventTap != nil
        }
        if let eventTap { CGEvent.tapEnable(tap: eventTap, enable: false) }
        inputEventTapAttempted = false
        return true
    }

    private static let keyEventTapCallback: CGEventTapCallBack = {
        _proxy, type, event, userInfo in
        guard let userInfo else { return Unmanaged.passUnretained(event) }
        let controller = Unmanaged<OverlayController>.fromOpaque(userInfo)
            .takeUnretainedValue()
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if controller.shieldEnabled, let eventTap = controller.eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
            }
            return Unmanaged.passUnretained(event)
        }
        guard controller.shieldEnabled,
              NSWorkspace.shared.frontmostApplication?.bundleIdentifier
                == resolveBundleID else {
            return Unmanaged.passUnretained(event)
        }
        if type == .flagsChanged {
            return Unmanaged.passUnretained(event)
        }
        if [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
            .otherMouseDown, .otherMouseUp].contains(type),
           controller.isOverlayControlEvent(event) {
            return Unmanaged.passUnretained(event)
        }
        // Keep macOS's application switcher available so the shield never
        // traps the user in Resolve. Other keyboard and session mouse input
        // is swallowed while Resolve is frontmost. AX calls are a separate
        // IPC path and cannot be intercepted by a Quartz event tap.
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        if type == .keyDown, event.flags.contains(.maskCommand),
           (keyCode == 48 || keyCode == 49) {
            return Unmanaged.passUnretained(event)
        }
        return nil
    }

    private func isOverlayControlEvent(_ event: CGEvent) -> Bool {
        guard let panel, let contentView = panel.contentView else { return false }
        let mainTop = NSScreen.screens.first(where: {
            ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")]
                as? NSNumber)?.uint32Value == CGMainDisplayID()
        })?.frame.maxY ?? NSScreen.screens.first?.frame.maxY ?? 0
        let quartz = event.location
        let cocoaPoint = NSPoint(x: quartz.x, y: mainTop - quartz.y)
        let windowPoint = panel.convertPoint(fromScreen: cocoaPoint)
        let contentPoint = contentView.convert(windowPoint, from: nil)
        return contentView.hitTest(contentPoint) is NSButton
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
