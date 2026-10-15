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
    let interruptPolicy: String
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
    private let takeoverButton = NSButton(title: "Take over", target: nil, action: nil)
    private let titleLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(labelWithString: "")
    private let elapsedLabel = NSTextField(labelWithString: "")
    private let messageLabel = NSTextField(labelWithString: "")
    private let stop = NSButton(title: "Stop safely", target: nil, action: nil)

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

        takeoverButton.bezelStyle = .rounded
        takeoverButton.font = .systemFont(ofSize: 11, weight: .semibold)
        takeoverButton.contentTintColor = .systemOrange
        takeoverButton.target = self
        takeoverButton.action = #selector(takeoverPressed)
        card.addSubview(takeoverButton)
        layoutCard(card)
        updateLabels()
    }

    required init?(coder: NSCoder) { nil }

    func update(title: String, detail: String, elapsed: String,
                message: String, canStop: Bool, shieldEnabled: Bool,
                takeoverTitle: String = "Take over",
                canTakeover: Bool = true) {
        self.title = title
        self.detail = detail
        self.elapsed = elapsed
        self.message = message
        self.cancelEnabled = canStop
        self.shieldEnabled = shieldEnabled
        takeoverButton.title = takeoverTitle
        takeoverButton.isEnabled = canTakeover
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
        takeoverButton.toolTip = "Request a safe stop, then return Resolve to manual control."
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
        for button in [takeoverButton, stop] {
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
        takeoverButton.frame = NSRect(x: 28 + buttonWidth, y: 18,
                                      width: buttonWidth, height: 26)
    }

    override func draw(_ dirtyRect: NSRect) {
        // The content itself is transparent; the small HUD card carries the
        // only visible treatment while this view catches Resolve clicks.
    }
}

private final class OverlayController: NSObject, NSApplicationDelegate {
    private let lockDir: URL
    private let logURL: URL
    private let leaseURL: URL
    private let socketURL: URL
    private let singletonFD: Int32
    private var panel: NSPanel?
    private var content: CatchView?
    private var timer: Timer?
    private var lastActivity: Activity?
    private var requestedStop = false
    private var shieldEnabled = true
    private var requestedHandoff = false
    private var handoffReady = false
    private var eventTap: CFMachPort?
    private var eventTapSource: CFRunLoopSource?
    private var inputEventTapAttempted = false
    private var brokerUnavailableSince: Date?
    private var inputShieldEnabled = false
    private var inputTapFailure = ""
    private var lastVisibilityState: Bool?
    private var lastWindowDiscoveryIssue: String?

    init(lockDir: URL) {
        self.lockDir = lockDir
        logURL = lockDir.appendingPathComponent("resolve-activity-overlay.log")
        leaseURL = lockDir.appendingPathComponent("resolve_instance.lease.json")
        socketURL = lockDir.appendingPathComponent("ren-resolved.sock")
        let fd = open(lockDir.appendingPathComponent("resolve-activity-overlay.lock").path,
                      O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        singletonFD = fd
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard singletonFD >= 0, flock(singletonFD, LOCK_EX | LOCK_NB) == 0 else {
            log("startup refused: another overlay owns the singleton lock")
            NSApp.terminate(nil)
            return
        }
        log("overlay started; waiting for Resolve activity")
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
        if FileManager.default.fileExists(atPath:
            lockDir.appendingPathComponent("resolve_handoff.fence").path) {
            requestedHandoff = true
        }
        if let handoff = brokerRequest(["op": "handoff_status"]),
           (handoff["fenced"] as? Bool) == true {
            requestedHandoff = true
            handoffReady = (handoff["ready"] as? Bool) == true
        }
        if requestedHandoff {
            let state = brokerRequest(["op": "handoff_status"])
            if (state?["ready"] as? Bool) == true && !handoffReady {
                handoffReady = true
                log("handoff ready: terminal job state and Resolve lease release verified")
            }
        }
        guard let target = resolveWindow() else {
            panel?.orderOut(nil)
            inputShieldEnabled = setInputEventTap(
                enabled: activity.mode == "exclusive" && !handoffReady)
            logVisibility(false, detail: "Resolve window was not found")
            return
        }
        let frame = target.frame
        let shouldShield = activity.mode == "exclusive" && !handoffReady
        let panelFrame = shouldShield ? frame : compactFrame(in: frame)
        if panel == nil { createPanel() }
        guard let panel else { return }
        if !target.frontmost {
            panel.orderOut(nil)
            inputShieldEnabled = setInputEventTap(enabled: shouldShield)
            shieldEnabled = false
            logVisibility(false, detail: "Resolve is not the frontmost application")
        } else {
            panel.setFrame(panelFrame, display: true)
            panel.level = .floating
            if let number = target.number {
                panel.order(.above, relativeTo: Int(number))
            } else {
                panel.orderFrontRegardless()
            }
            let panelVisible = verifyPanelVisible(target: target)
            logVisibility(panelVisible, detail: "panel is not above Resolve on its display")
            inputShieldEnabled = setInputEventTap(enabled: shouldShield)
            shieldEnabled = shouldShield && panelVisible
        }
        let canStop = activity.cancellable
        let elapsed = max(0, Int(Date().timeIntervalSince1970 - activity.started))
        let extra = activity.additionalJobs > 0
            ? " +\(activity.additionalJobs) queued/running" : ""
        let detail = activity.purpose + extra
        let message: String
        if handoffReady {
            message = "Ren has stopped and released Resolve. Return to Ren when you are ready."
        } else if activity.mode == "handoff" {
            message = "The handoff fence is set. Ren is paused; reconnect the broker to verify the lease and return control."
        } else if requestedHandoff {
            message = "Takeover requested. Resolve stays shielded until the job ends and its lease is released."
        } else if requestedStop {
            message = "Stop request accepted. Waiting for the safe point and lease release."
        } else if shouldShield && !target.frontmost {
            message = "Ren holds Resolve. Bring Resolve forward to see the shield controls."
        } else if shouldShield && !shieldEnabled {
            message = "Overlay visibility failed. See resolve-activity-overlay.log; Resolve remains leased."
        } else if activity.mode == "shared" {
            message = "Read-only Ren job. Resolve input remains available."
        } else if shouldShield && !inputShieldEnabled {
            message = "Mouse shield active. \(inputTapFailure.isEmpty ? "Keyboard shielding is unavailable." : inputTapFailure). Enable the named permission for Ren's Resolve activity overlay in System Settings > Privacy & Security."
        } else if canStop {
            message = "Safe Stop is available at: " + activity.interruptPolicy
        } else {
            message = "No safe running Stop path. Resolve stays shielded until the job finishes."
        }
        content?.update(title: activity.description,
                        detail: detail,
                        elapsed: String(format: "Elapsed %02d:%02d", elapsed / 60, elapsed % 60),
                        message: message,
                        canStop: canStop && !requestedStop && !requestedHandoff,
                        shieldEnabled: shieldEnabled,
                        takeoverTitle: handoffReady ? "Return to Ren"
                            : requestedHandoff ? "Waiting safely…" : "Take over",
                        canTakeover: handoffReady ||
                            (activity.mode == "exclusive" && !requestedHandoff))
        content?.cancelAction = { [weak self] in self?.sendStop(jobID: activity.jobID) }
        content?.takeoverAction = { [weak self] in
            if self?.handoffReady == true { self?.returnToRen() }
            else { self?.takeOver() }
        }
    }

    private func createPanel() {
        let window = NSPanel(contentRect: NSRect(x: 0, y: 0,
                                                 width: panelWidth,
                                                 height: panelHeight),
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        window.level = .floating
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.ignoresMouseEvents = false
        window.hidesOnDeactivate = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isReleasedWhenClosed = false
        let view = CatchView(frame: NSRect(x: 0, y: 0,
                                           width: panelWidth,
                                           height: panelHeight))
        view.autoresizingMask = [.width, .height]
        window.contentView = view
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

    private func verifyPanelVisible(target: ResolveWindow) -> Bool {
        guard let panel, panel.isVisible, panel.frame.intersects(target.frame),
              let resolveNumber = target.number else { return false }
        let windows = (CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]) ?? []
        let order = windows.compactMap {
            $0[kCGWindowNumber as String] as? Int
        }
        guard let panelIndex = order.firstIndex(of: panel.windowNumber),
              let resolveIndex = order.firstIndex(of: Int(resolveNumber)) else {
            return false
        }
        return panelIndex < resolveIndex
    }

    private func logVisibility(_ visible: Bool, detail: String) {
        guard lastVisibilityState != visible else { return }
        lastVisibilityState = visible
        log(visible ? "panel visibility verified above Resolve" : "panel visibility failed: \(detail)")
    }

    private func logWindowDiscoveryIssue(_ detail: String) {
        guard lastWindowDiscoveryIssue != detail else { return }
        lastWindowDiscoveryIssue = detail
        log("Resolve window discovery: \(detail)")
    }

    private func log(_ message: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        do {
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path,
                                               contents: nil)
            }
            let handle = try FileHandle(forWritingTo: logURL)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } catch {
            fputs("resolve-activity-overlay: \(message) (log write failed: \(error))\n",
                  stderr)
        }
    }

    private func readLease() -> Lease? {
        guard let data = try? Data(contentsOf: leaseURL) else { return nil }
        return try? JSONDecoder().decode(Lease.self, from: data)
    }

    private func currentActivity() -> Activity? {
        guard let lease = readLease() else {
            if let shared = currentSharedActivity() { return shared }
            guard let data = try? Data(contentsOf: lockDir.appendingPathComponent(
                "resolve_handoff.fence")),
                let fence = try? JSONSerialization.jsonObject(with: data)
                    as? [String: Any] else { return nil }
            let fenceID = (fence["id"] as? String) ?? "unknown"
            let ready = (brokerRequest(["op": "handoff_status"])?["ready"]
                         as? Bool) == true
            handoffReady = ready
            return Activity(jobID: fenceID, purpose: "Manual Resolve control",
                            owner: "You", started: Date().timeIntervalSince1970,
                            cancellable: false, interruptPolicy: "",
                            additionalJobs: 0,
                            description: "Resolve is yours. Ren is paused.",
                            mode: "handoff")
        }
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
                    let interruptPolicy = (reply["interrupt_policy"] as? String) ?? ""
                    return Activity(
                        jobID: id, purpose: purpose,
                        owner: (job["owner"] as? String) ?? lease.owner,
                        started: (job["started"] as? Double)
                            ?? (job["submitted"] as? Double)
                            ?? lease.since,
                        cancellable: !interruptPolicy.isEmpty,
                        interruptPolicy: interruptPolicy,
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
            interruptPolicy: "",
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
            interruptPolicy: "",
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
        let candidates = NSWorkspace.shared.runningApplications.filter {
            $0.bundleIdentifier == resolveBundleID
        }
        let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        guard let app = candidates.first(where: {
            $0.processIdentifier == frontmostPID
        }) ?? candidates.first else {
            let frontmostPIDText = frontmostPID.map { String($0) } ?? "unknown"
            logWindowDiscoveryIssue(
                "no running app with bundle \(resolveBundleID); frontmost pid \(frontmostPIDText)")
            return nil
        }
        let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements],
                                                   kCGNullWindowID) as? [[String: Any]]) ?? []
        func integer(_ value: Any?) -> Int? {
            if let number = value as? NSNumber { return number.intValue }
            if let number = value as? Int { return number }
            return nil
        }
        func scalar(_ value: Any?) -> CGFloat? {
            if let number = value as? NSNumber { return CGFloat(number.doubleValue) }
            if let number = value as? Double { return CGFloat(number) }
            if let number = value as? Int { return CGFloat(number) }
            return nil
        }
        let ownerPID = Int(app.processIdentifier)
        let ownedWindows = windows.filter {
            integer($0[kCGWindowOwnerPID as String]) == ownerPID
        }
        var displayCount: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &displayCount) == .success,
              displayCount > 0 else {
            logWindowDiscoveryIssue("could not enumerate active displays for Resolve pid \(ownerPID)")
            return nil
        }
        var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(displayCount))
        guard CGGetActiveDisplayList(displayCount, &displayIDs, &displayCount) == .success else {
            logWindowDiscoveryIssue("could not read active display IDs for Resolve pid \(ownerPID)")
            return nil
        }
        let matches = windows.compactMap { info -> (NSRect, Int32)? in
            guard integer(info[kCGWindowOwnerPID as String]) == ownerPID,
                  integer(info[kCGWindowLayer as String]) == 0,
                  let bounds = info[kCGWindowBounds as String] as? [String: Any],
                  let x = scalar(bounds["X"]), let y = scalar(bounds["Y"]),
                  let width = scalar(bounds["Width"]),
                  let height = scalar(bounds["Height"]),
                  width > 300, height > 200,
                  let number = integer(info[kCGWindowNumber as String]) else { return nil }
            let quartzFrame = CGRect(x: x, y: y, width: width, height: height)
            let screenMatches = displayIDs.compactMap { displayID -> (CGFloat, NSRect, CGRect)? in
                guard let screen = NSScreen.screens.first(where: {
                    ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")]
                        as? NSNumber)?.uint32Value == displayID
                }) else { return nil }
                let displayBounds = CGDisplayBounds(displayID)
                let intersection = quartzFrame.intersection(displayBounds)
                let area = max(0, intersection.width) * max(0, intersection.height)
                return (area, screen.frame, displayBounds)
            }
            guard let display = screenMatches.max(by: { $0.0 < $1.0 }),
                  display.0 > 0 else { return nil }
            let cocoaFrame = NSRect(
                x: display.1.minX + (x - display.2.minX),
                y: display.1.maxY - (y - display.2.minY) - height,
                width: width, height: height)
            return (cocoaFrame, Int32(number))
        }
        guard let largest = matches.max(by: {
            $0.0.width * $0.0.height < $1.0.width * $1.0.height
        }) else {
            let summary = ownedWindows.map { info -> String in
                let name = info[kCGWindowName as String] as? String ?? "unnamed"
                let layer = integer(info[kCGWindowLayer as String]).map(String.init) ?? "?"
                let bounds = info[kCGWindowBounds as String] as? [String: Any] ?? [:]
                let x = scalar(bounds["X"]).map(String.init(describing:)) ?? "?"
                let y = scalar(bounds["Y"]).map(String.init(describing:)) ?? "?"
                let width = scalar(bounds["Width"]).map(String.init(describing:)) ?? "?"
                let height = scalar(bounds["Height"]).map(String.init(describing:)) ?? "?"
                return "\(name) layer=\(layer) bounds=\(x),\(y),\(width),\(height)"
            }.joined(separator: "; ")
            logWindowDiscoveryIssue(
                "no eligible Resolve window for pid \(ownerPID); owned on-screen windows: \(summary.isEmpty ? "none" : summary)")
            return nil
        }
        lastWindowDiscoveryIssue = nil
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
            let state = (result["job"] as? [String: Any])?["state"] as? String
            if let state, ["done", "failed", "rejected", "cancelled"].contains(state) {
                updateMessage("Job already ended (\(state)). Waiting for Resolve's lease to release.")
                log("Stop pressed after broker job \(jobID) had ended as \(state)")
            } else {
                requestedStop = true
                log("Stop request accepted for broker job \(jobID); waiting for terminal receipt and lease release")
            }
        }
    }

    private func updateMessage(_ message: String) {
        content?.update(title: lastActivity?.description ?? "Ren is using Resolve",
                        detail: lastActivity?.purpose ?? "",
                        elapsed: "",
                        message: message,
                        canStop: false, shieldEnabled: shieldEnabled,
                        takeoverTitle: handoffReady ? "Return to Ren"
                            : requestedHandoff ? "Waiting safely…" : "Take over",
                        canTakeover: handoffReady ||
                            (!requestedHandoff && lastActivity?.mode == "exclusive"))
    }

    private func takeOver() {
        guard let result = brokerRequest(["op": "handoff"]) else {
            updateMessage("The broker did not accept Take over. Resolve remains shielded.")
            return
        }
        if let error = result["error"] as? String {
            updateMessage("Take over was refused: \(error)")
            return
        }
        requestedHandoff = true
        handoffReady = false
        requestedStop = false
        log("Takeover requested; broker fence is set before safe stop and lease release")
        updateMessage("Takeover accepted. Resolve stays shielded until the active job ends and the lease is released.")
    }

    private func returnToRen() {
        guard let result = brokerRequest(["op": "return_to_ren"]),
              (result["returned"] as? Bool) == true else {
            updateMessage("Resolve is still in use. Ren remains paused.")
            return
        }
        requestedHandoff = false
        handoffReady = false
        log("human handoff fence explicitly cleared; Resolve returned to Ren")
    }

    @discardableResult
    private func setInputEventTap(enabled: Bool) -> Bool {
        if enabled {
            let needs = [
                !AXIsProcessTrusted() ? "Accessibility" : nil,
                !CGPreflightListenEventAccess() ? "Input Monitoring" : nil,
            ].compactMap { $0 }
            guard needs.isEmpty else {
                let failure = "macOS permission missing: \(needs.joined(separator: " and "))"
                if inputTapFailure != failure { log(failure) }
                inputTapFailure = failure
                inputShieldEnabled = false
                return false
            }
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
                guard let eventTap else {
                    inputTapFailure = "macOS refused the Resolve keyboard and mouse event tap"
                    log(inputTapFailure)
                    return false
                }
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
            inputTapFailure = ""
            inputShieldEnabled = eventTap != nil
            return inputShieldEnabled
        }
        if let eventTap { CGEvent.tapEnable(tap: eventTap, enable: false) }
        inputEventTapAttempted = false
        inputShieldEnabled = false
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
        guard controller.inputShieldEnabled,
              NSWorkspace.shared.frontmostApplication?.bundleIdentifier
                == resolveBundleID else {
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
        let written = body.withUnsafeBytes {
            Darwin.write(fd, $0.baseAddress, $0.count)
        }
        guard written == body.count else { return nil }
        var response = [UInt8]()
        while response.count < 1_048_576 {
            var chunk = [UInt8](repeating: 0, count: 4096)
            let count = chunk.withUnsafeMutableBytes {
                Darwin.read(fd, $0.baseAddress, $0.count)
            }
            guard count > 0 else { return nil }
            response.append(contentsOf: chunk.prefix(count))
            if response.contains(0x0A) { break }
        }
        guard response.last == 0x0A else { return nil }
        let data = Data(response.dropLast())
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}

let arguments = CommandLine.arguments
let env = ProcessInfo.processInfo.environment
let envLockDir = env["PIPELINE_RESOLVE_INSTANCE_DIR"] ?? env["PIPELINE_RESOLVE_LOCK_DIR"]
let lockPath = arguments.dropFirst().first ?? envLockDir ?? NSTemporaryDirectory()
private let controller = OverlayController(lockDir: URL(fileURLWithPath: lockPath, isDirectory: true))
let application = NSApplication.shared
application.delegate = controller
application.run()
