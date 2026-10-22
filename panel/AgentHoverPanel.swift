// agent-hover hover panel (POC 2).
//
// A small pill in the top-right corner (draggable) shows counts of agent tabs.
// Hover: it expands to a list of all agent tabs, most recently active first (buried ones are
// dimmed and marked ▾). The order is sorted when the list opens and stays while it is open.
// Each row has an agent mark (built-in table, overridden by ~/.agent-hover/agents.json).
// Edge bar: drag the pill to the right edge of the screen: the panel becomes a thin bar in the
// color of the most urgent state (waiting > done > working); hover the bar for the full panel;
// drag the pill away from the edge to undock.
// Search: the magnifier on the open list starts search mode: the panel takes the keyboard and
// filters the rows by tab name and agent folder; the "chat" switch also searches the chat text
// (agent_hover/search.py). Esc, a click outside or a click on a row ends it; Enter raises the first row.
// Click a row: ask the daemon to raise that tab (unbury, select, bring iTerm2 front).
//
// Data: reads ~/.agent-hover/panel.json (written by the daemon each second).
// Clicks: writes ~/.agent-hover/requests/<uuid>.json (the daemon handles it within 0.2 s).
// The panel never takes keyboard focus (non-activating), and has no Dock icon.
//
// Build: swiftc -O -swift-version 5 panel/AgentHoverPanel.swift -o .build/agent-hover-panel

import AppKit

let home: URL = {
    if let h = ProcessInfo.processInfo.environment["AGENT_HOVER_HOME"], !h.isEmpty {
        return URL(fileURLWithPath: h)
    }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agent-hover")
}()
let panelFile = home.appendingPathComponent("panel.json")
let posFile = home.appendingPathComponent("panel-pos.json")
let requestDir = home.appendingPathComponent("requests")
let agentsFile = home.appendingPathComponent("agents.json")
let configFile = home.appendingPathComponent("config.json")
let peekFile = home.appendingPathComponent("peek.json")

// Agent mark by process name: a short text mark and a color ("#RRGGBB", or "gray").
// ~/.agent-hover/agents.json overrides or adds entries:
//   {"codex": {"mark": ">_", "color": "#8E8E93"}}
let builtinMarks: [String: (mark: String, color: String)] = [
    "claude": ("✳", "#D97757"),
    "codex": (">_", "gray"),
    "agy": ("▲", "#4C8BF5"),
    "gemini": ("✦", "#4285F4"),
    "cursor-agent": ("⬢", "gray"),
    "copilot": ("cp", "#8957E5"),
    "aider": ("ai", "#3FB950"),
    "opencode": ("oc", "gray"),
    "qwen": ("Q", "#7C5CFF"),
]
var agentMarks = builtinMarks
var agentsFileDate: Date?

/// Reload agents.json when its modification time changes (or it goes away).
func reloadAgentMarks() {
    let date = (try? FileManager.default.attributesOfItem(atPath: agentsFile.path))?[.modificationDate] as? Date
    if date == agentsFileDate { return }
    agentsFileDate = date
    var marks = builtinMarks
    if let data = try? Data(contentsOf: agentsFile),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] {
        for (agent, v) in obj {
            marks[agent] = (v["mark"] ?? marks[agent]?.mark ?? String(agent.prefix(1)),
                            v["color"] ?? marks[agent]?.color ?? "gray")
        }
    }
    agentMarks = marks
}

func color(hex: String) -> NSColor {
    let s = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
    guard s.count == 6, let v = UInt32(s, radix: 16) else { return infoColor }
    return NSColor(srgbRed: CGFloat(v >> 16 & 0xFF) / 255, green: CGFloat(v >> 8 & 0xFF) / 255,
                   blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}

/// The agent's mark; an unknown agent gets its first letter in gray.
func agentBadge(_ agent: String) -> NSAttributedString {
    let m = agentMarks[agent] ?? (String(agent.prefix(1)), "gray")
    return text(m.mark, color(hex: m.color), bold: true, size: 11)
}

struct Row: Decodable {
    let session: String
    let name: String
    let agent: String
    let state: String
    let detail: String?
    let since: Double
    let last: Double?  // time of the latest hook event (older daemons do not send it)
    let buried: Bool
    let focused: Bool
    let question: String?  // what a waiting agent asks (older daemons do not send it)
    let cwd: String?       // the agent's folder (older daemons do not send it)
    var pinned: Bool?      // pinned in the panel: never parked, listed first
    let model: String?     // the model from the agent's transcript, e.g. "gpt-5.6-sol (openai)"
    let unseen: Bool?      // done, and the person has not looked at the tab since
}

struct Snapshot: Decodable {
    let ts: Double
    let apply: Bool
    let rows: [Row]
}

// Sizes follow config.json "panel": {"font_size": 12, "list_width": 440}; 12 is the base size.
var fontSize: CGFloat = 12
var listWidth: CGFloat = 440
func fs(_ base: CGFloat) -> CGFloat { (base * fontSize / 12).rounded() }
var pillHeight: CGFloat { fs(26) }
var rowHeight: CGFloat { fs(22) }
var configFileDate: Date?

/// Reload config.json when it changes. Returns true when the panel sizes changed.
func reloadPanelConfig() -> Bool {
    let date = (try? FileManager.default.attributesOfItem(atPath: configFile.path))?[.modificationDate] as? Date
    if date == configFileDate { return false }
    configFileDate = date
    var size: CGFloat = 12, width: CGFloat = 440
    if let data = try? Data(contentsOf: configFile),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let panelCfg = obj["panel"] as? [String: Any] {
        if let v = panelCfg["font_size"] as? Double { size = CGFloat(min(24, max(9, v))) }
        if let v = panelCfg["list_width"] as? Double { width = CGFloat(min(1200, max(280, v))) }
    }
    let changed = size != fontSize || width != listWidth
    fontSize = size
    listWidth = width
    return changed
}
let pad: CGFloat = 8
// Gray text, brighter than .secondaryLabelColor (hard to read on the HUD background).
let infoColor = NSColor.labelColor.withAlphaComponent(0.8)  // state, age, counts, marks
let dimColor = NSColor.labelColor.withAlphaComponent(0.6)   // buried tab names, "dry run"

/// The state mark as text: a waiting agent gets a question icon (SF Symbol), the others a glyph.
func stateGlyph(_ state: String, size: CGFloat = 12) -> NSAttributedString {
    let (g, c) = mark(state)
    if state == "waiting",
       let img = NSImage(systemSymbolName: "questionmark.circle.fill", accessibilityDescription: "waiting")?
           .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: fs(size), weight: .bold)
               .applying(NSImage.SymbolConfiguration(paletteColors: [.white, .systemOrange]))) {
        let a = NSTextAttachment()
        a.image = img
        a.bounds = NSRect(x: 0, y: -2, width: img.size.width, height: img.size.height)
        return NSAttributedString(attachment: a)
    }
    return text(g, c, bold: true, size: size)
}

func mark(_ state: String) -> (String, NSColor) {
    switch state {
    case "waiting": return ("●", .systemOrange)
    case "done": return ("✓", .systemGreen)
    case "working": return ("◐", .systemYellow)
    default: return ("○", infoColor)
    }
}

/// Always 3 characters ("  5s", " 42m"), padded with figure spaces: with monospaced digits the
/// width stays the same each second, so the pill does not change its width as the time counts.
func age(_ since: Double) -> String {
    let s = max(0, Date().timeIntervalSince1970 - since)
    let v = s < 60 ? "\(Int(s))s" : s < 3600 ? "\(Int(s / 60))m" : "\(Int(s / 3600))h"
    return String(repeating: "\u{2007}", count: max(0, 3 - v.count)) + v
}

func text(_ s: String, _ color: NSColor, bold: Bool = false, size: CGFloat = 12) -> NSAttributedString {
    NSAttributedString(string: s, attributes: [
        .font: NSFont.monospacedDigitSystemFont(ofSize: fs(size), weight: bold ? .bold : .regular),
        .foregroundColor: color,
    ])
}

// Spaces: the private SkyLight calls that Hammerspoon's hs.spaces also uses (read only).
@_silgen_name("CGSMainConnectionID") func CGSMainConnectionID() -> Int32
@_silgen_name("CGSCopyManagedDisplaySpaces") func CGSCopyManagedDisplaySpaces(_ cid: Int32) -> CFArray

/// True when the active Space of the screen's display is that display's first desktop Space
/// (type 0; a full-screen app Space is type 4). nil when unknown: the caller then shows the panel.
func onFirstSpace(of screen: NSScreen?) -> Bool? {
    guard let displays = CGSCopyManagedDisplaySpaces(CGSMainConnectionID()) as? [[String: Any]],
          var d = displays.first else { return nil }
    if displays.count > 1, let screen = screen,
       let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
       let uuid = CGDisplayCreateUUIDFromDisplayID(num.uint32Value)?.takeRetainedValue(),
       let match = displays.first(where: {
           ($0["Display Identifier"] as? String) == (CFUUIDCreateString(nil, uuid) as String?)
       }) {
        d = match
    }
    guard let current = (d["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int,
          let spaces = d["Spaces"] as? [[String: Any]],
          let first = spaces.first(where: { ($0["type"] as? Int) == 0 })?["ManagedSpaceID"] as? Int
    else { return nil }
    return current == first
}

final class Panel: NSPanel {
    var allowKey = false  // true only in search mode, so the search field can take the keyboard
    override var canBecomeKey: Bool { allowKey }
    override var canBecomeMain: Bool { false }
}

/// The collapsed pill: the most recent tab and the counts. A drag moves the panel;
/// a click (no move) raises the pill's tab.
final class PillView: NSView {
    var content = NSAttributedString()
    var onDragEnd: (() -> Void)?
    var onClick: (() -> Void)?

    override func draw(_ dirtyRect: NSRect) {
        let size = content.size()
        content.draw(at: NSPoint(x: bounds.maxX - size.width - pad - 2, y: (bounds.height - size.height) / 2))
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    // Own drag loop (not performDrag), so a click and a drag can be told apart. The 1 s refresh
    // timer does not run in this loop, so it cannot move the panel back during the drag.
    override func mouseDown(with event: NSEvent) {
        guard let win = window else { return }
        let start = NSEvent.mouseLocation
        let origin = win.frame.origin
        var moved = false
        while let e = win.nextEvent(matching: [.leftMouseDragged, .leftMouseUp]) {
            let p = NSEvent.mouseLocation
            if !moved && hypot(p.x - start.x, p.y - start.y) > 3 { moved = true }
            if moved { win.setFrameOrigin(NSPoint(x: origin.x + p.x - start.x, y: origin.y + p.y - start.y)) }
            if e.type == .leftMouseUp { break }
        }
        if moved { onDragEnd?() } else { onClick?() }
    }
}

// Right-hand columns of a row: the widest time ("88m") and the agent mark.
var ageColumnW: CGFloat { ["88s", "88m", "88h"].map { text($0, .labelColor, size: 11).size().width }.max()! }
var markColumnW: CGFloat { fs(18) }

func drawCentered(_ s: NSAttributedString, centerX: CGFloat, y: CGFloat) {
    s.draw(at: NSPoint(x: centerX - s.size().width / 2, y: y))
}

/// The pin mark for pinned rows (SF Symbol, tinted).
func pinIcon() -> NSImage? {
    NSImage(systemSymbolName: "pin.fill", accessibilityDescription: "pinned")?
        .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: fs(10), weight: .bold)
            .applying(NSImage.SymbolConfiguration(paletteColors: [.systemOrange])))
}

/// One agent tab in the expanded list. Click raises it; right-click: pin or unpin.
final class RowView: NSView {
    let row: Row
    let note: String?  // search mode: what matched (folder or chat line), after the name
    let onClick: (Row) -> Void
    var onPin: ((Row, Bool) -> Void)?
    private var hover = false

    init(row: Row, frame: NSRect, note: String? = nil, onClick: @escaping (Row) -> Void) {
        self.row = row
        self.note = note
        self.onClick = onClick
        super.init(frame: frame)
        toolTip = [row.agent, row.model].compactMap { $0 }.joined(separator: " · ")
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    required init?(coder: NSCoder) { fatalError() }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseEntered(with event: NSEvent) { hover = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hover = false; needsDisplay = true }
    override func mouseDown(with event: NSEvent) { onClick(row) }

    override func menu(for event: NSEvent) -> NSMenu? {  // right-click or control-click
        let pinned = row.pinned == true
        let m = NSMenu()
        let item = NSMenuItem(title: pinned ? "Unpin" : "Pin (never park this tab)",
                              action: #selector(togglePin), keyEquivalent: "")
        item.target = self
        item.image = NSImage(systemSymbolName: pinned ? "pin.slash" : "pin", accessibilityDescription: nil)
        m.addItem(item)
        return m
    }

    @objc func togglePin() { onPin?(row, row.pinned != true) }

    override func draw(_ dirtyRect: NSRect) {
        if hover {
            NSColor.selectedContentBackgroundColor.withAlphaComponent(0.35).setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 4, dy: 1), xRadius: 5, yRadius: 5).fill()
        }
        let dim: NSColor = row.buried ? dimColor : .labelColor
        let y = (bounds.height - fs(15)) / 2
        if row.pinned == true, let pin = pinIcon() {
            pin.draw(in: NSRect(x: pad - 1, y: y + 1, width: pin.size.width, height: pin.size.height))
        } else if row.unseen == true {  // "new": done, not looked at yet
            NSColor.systemBlue.setFill()
            let d = fs(7)
            NSBezierPath(ovalIn: NSRect(x: pad, y: (bounds.height - d) / 2, width: d, height: d)).fill()
        } else {
            text(row.buried ? "▾" : (row.focused ? "▸" : " "), infoColor).draw(at: NSPoint(x: pad, y: y))
        }
        // Fixed columns, each mark centered in its own column, so marks of different widths line up.
        drawCentered(stateGlyph(row.state), centerX: pad + fs(20), y: y)
        // Time since the latest hook event: the same time as the pill, and the sort key.
        let ageText = text(age(row.last ?? row.since), infoColor, size: 11)
        ageText.draw(at: NSPoint(x: bounds.width - pad - ageText.size().width, y: y + 1))
        let markX = bounds.width - pad - ageColumnW - 6 - markColumnW / 2
        drawCentered(agentBadge(row.agent), centerX: markX, y: y + 1)
        let rightW = ageColumnW + 6 + markColumnW
        var label = row.name
        if let d = row.detail, !d.isEmpty, row.state == "waiting" { label += "  — \(d)" }
        let nameRect = NSRect(x: pad + fs(32), y: y, width: bounds.width - rightW - pad * 2 - fs(40), height: fs(16))
        let style = NSMutableParagraphStyle()
        style.lineBreakMode = .byTruncatingTail
        let s = NSMutableAttributedString(string: label, attributes: [
            .font: NSFont.systemFont(ofSize: fs(12)), .foregroundColor: dim, .paragraphStyle: style,
        ])
        if let note = note {
            s.append(NSAttributedString(string: "  \(note)", attributes: [
                .font: NSFont.systemFont(ofSize: fs(11)), .foregroundColor: infoColor, .paragraphStyle: style,
            ]))
        }
        s.draw(in: nameRect)
    }
}

/// Root view: hover anywhere on the panel expands it.
final class RootView: NSVisualEffectView {
    var onHover: ((Bool) -> Void)?

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    override func mouseEntered(with event: NSEvent) { onHover?(true) }
    override func mouseExited(with event: NSEvent) { onHover?(false) }
}

/// Rows are laid out top-down in the scroll view's document.
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

final class Controller: NSObject, NSSearchFieldDelegate {
    let panel: Panel
    let root = RootView()
    let pill = PillView()
    var rowViews: [RowView] = []
    let scroll = NSScrollView()   // the row list; scrolls when it is taller than the screen allows
    let listView = FlippedView()
    var snapshot: Snapshot?
    var expanded = false
    var watchTimer: Timer?
    var outsideSince: Date?
    var inLayout = false  // our own setFrame moves the panel too: not a drag
    var order: [String: Int] = [:]  // row position by session, kept while the list is open
    var pillRow: Row?  // the tab the pill shows; a click on the pill raises it
    // A pin or unpin shows at once: kept here until panel.json agrees (or 5 s pass).
    var pendingPins: [String: (on: Bool, since: Date)] = [:]
    // Question preview: a second small panel beside the list, for the waiting row under the mouse.
    let preview = Panel(contentRect: .zero, styleMask: [.nonactivatingPanel, .borderless],
                        backing: .buffered, defer: true)
    let previewText = NSTextField(wrappingLabelWithString: "")
    var previewKey: String?
    // Search mode
    let searchIcon = NSButton()
    let searchField = NSSearchField()
    let chatToggle = NSButton(checkboxWithTitle: "chat", target: nil, action: nil)
    let searchStatus = NSTextField(labelWithString: "")
    var searchMode = false
    var chatResults: [String: String?] = [:]  // session -> chat snippet, for chatQuery
    var chatQuery = ""
    var chatBusy = false
    var chatProcess: Process?
    var chatTimer: Timer?

    var anchor = NSPoint.zero  // top-right corner of the panel, in screen coordinates
    var docked = false         // edge bar mode: at the right edge of the screen
    let barView = NSView()     // the thin bar, shown when docked and not expanded
    let barW: CGFloat = 6

    override init() {
        panel = Panel(contentRect: NSRect(x: 0, y: 0, width: 120, height: pillHeight),
                      styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        super.init()
        // Only on the first desktop Space (not on full-screen app Spaces or other Spaces):
        // no .canJoinAllSpaces, so the panel stays on its Space; syncSpace() puts it there.
        panel.level = .statusBar
        panel.collectionBehavior = [.ignoresCycle]
        panel.hidesOnDeactivate = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        root.material = .hudWindow
        root.state = .active
        root.blendingMode = .behindWindow
        root.wantsLayer = true
        root.layer?.cornerRadius = 9
        root.layer?.masksToBounds = true
        root.onHover = { [weak self] inside in self?.hover(inside) }
        pill.onDragEnd = { [weak self] in self?.dragEnded() }
        pill.onClick = { [weak self] in
            if let self = self, let r = self.pillRow { self.raise(r) }
        }
        // performDrag can return before the move ends, so also save on each window move.
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: panel, queue: .main) {
            [weak self] _ in
            guard let self = self, !self.inLayout else { return }
            self.savePosition()
        }
        panel.contentView = root
        root.addSubview(pill)
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.documentView = listView
        root.addSubview(scroll)
        barView.wantsLayer = true
        barView.layer?.cornerRadius = barW / 2
        barView.isHidden = true
        root.addSubview(barView)
        setUpPreview()
        setUpSearch()
        anchor = loadPosition()
        expanded = alwaysExpanded
        if alwaysExpanded {
            watchTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                self?.checkOutside()
            }
        }
        refresh()
        applyForcedSearch()
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.activeSpaceDidChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.syncSpace() }
        Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func loadPosition() -> NSPoint {
        if let data = try? Data(contentsOf: posFile),
           let p = try? JSONSerialization.jsonObject(with: data) as? [String: Double],
           let x = p["x"], let y = p["y"],
           NSScreen.screens.contains(where: { $0.frame.insetBy(dx: -1, dy: -1).contains(NSPoint(x: x - 1, y: y - 1)) }) {
            docked = (p["docked"] ?? 0) == 1
            return NSPoint(x: x, y: y)
        }
        let f = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        return NSPoint(x: f.maxX - 8, y: f.maxY - 8)
    }

    /// A drag ended: dock when the panel's right side is within 12 px of the screen's right edge.
    func dragEnded() {
        savePosition()
        if let vis = (panel.screen ?? NSScreen.main)?.visibleFrame {
            docked = panel.frame.maxX >= vis.maxX - 12
            if docked { anchor.x = vis.maxX }
        }
        savePosition(keepAnchor: true)
        layout()
    }

    /// The bar's color: the most urgent state among the shown tabs.
    func barColor() -> NSColor {
        guard let snap = snapshot, fresh else { return infoColor }
        let states = Set(snap.rows.filter { !$0.buried }.map { $0.state })
        for s in ["waiting", "done", "working"] where states.contains(s) { return mark(s).1 }
        return infoColor
    }

    func savePosition(keepAnchor: Bool) {
        if !keepAnchor { anchor = NSPoint(x: panel.frame.maxX, y: panel.frame.maxY) }
        try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        let json = "{\"x\": \(Double(anchor.x)), \"y\": \(Double(anchor.y)), \"docked\": \(docked ? 1 : 0)}"
        try? json.data(using: .utf8)?.write(to: posFile, options: .atomic)
    }

    func savePosition() {
        anchor = NSPoint(x: panel.frame.maxX, y: panel.frame.maxY)
        try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        let json = "{\"x\": \(Double(anchor.x)), \"y\": \(Double(anchor.y)), \"docked\": \(docked ? 1 : 0)}"
        try? json.data(using: .utf8)?.write(to: posFile, options: .atomic)
    }

    // AGENT_HOVER_PANEL_EXPANDED=1 keeps the list open (for screenshots and tests).
    let alwaysExpanded = ProcessInfo.processInfo.environment["AGENT_HOVER_PANEL_EXPANDED"] == "1"

    // Enter expands. Leaving is found by polling the cursor while the list is open:
    // the exit event can be lost when the panel resizes or rebuilds its rows under the cursor.
    func hover(_ inside: Bool) {
        if alwaysExpanded || !inside || expanded { return }
        expanded = true
        layout()
        outsideSince = nil
        watchTimer?.invalidate()
        watchTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            self?.checkOutside()
        }
    }

    func checkOutside() {
        if alwaysExpanded && previewForced { updatePreview(); return }
        if searchMode { updatePreview(); return }  // typing: keep the list open
        if panel.frame.contains(NSEvent.mouseLocation) { outsideSince = nil; updatePreview(); return }
        hidePreview()
        if alwaysExpanded { return }
        let now = Date()
        if outsideSince == nil { outsideSince = now }
        if now.timeIntervalSince(outsideSince!) >= 0.4 { collapse() }
    }

    func collapse() {
        if searchMode { endSearch() }
        hidePreview()
        watchTimer?.invalidate()
        watchTimer = nil
        expanded = alwaysExpanded
        order = [:]
        layout()
    }

    /// One list by the latest hook event, newest first; buried tabs mix in (dimmed, marked ▾).
    /// While the list is open, rows keep their place so a row does not move under the cursor;
    /// a new row goes to the end.
    func sorted(_ rows: [Row]) -> [Row] {
        func key(_ r: Row) -> (Int, Double, String) { (r.pinned == true ? 0 : 1, -(r.last ?? r.since), r.name) }
        if order.isEmpty || alwaysExpanded {
            let fresh = rows.sorted { key($0) < key($1) }
            order = Dictionary(uniqueKeysWithValues: fresh.enumerated().map { ($1.session, $0) })
        }
        func pos(_ r: Row) -> Int { order[r.session] ?? Int.max }
        return rows.sorted { pos($0) != pos($1) ? pos($0) < pos($1) : key($0) < key($1) }
    }

    func setUpPreview() {
        preview.level = .statusBar
        preview.collectionBehavior = [.ignoresCycle]
        preview.isOpaque = false
        preview.backgroundColor = .clear
        preview.hasShadow = true
        preview.ignoresMouseEvents = true
        let back = NSVisualEffectView()
        back.material = .hudWindow
        back.state = .active
        back.blendingMode = .behindWindow
        back.wantsLayer = true
        back.layer?.cornerRadius = 9
        back.layer?.masksToBounds = true
        preview.contentView = back
        previewText.maximumNumberOfLines = 20
        previewText.cell?.truncatesLastVisibleLine = true
        back.addSubview(previewText)
    }

    /// The mouse is on a waiting row (or a chat match in search mode): show its question beside the list (left of the panel, or
    /// right when there is no room). Any other place: hide it.
    func updatePreview() {
        let inWindow = panel.convertPoint(fromScreen: NSEvent.mouseLocation)
        let inList = scroll.convert(inWindow, from: nil)
        let p = scroll.bounds.contains(inList) ? listView.convert(inWindow, from: nil) : NSPoint(x: -1, y: -1)
        let forced = alwaysExpanded && previewForced
            ? rowViews.first(where: { forcedPeek ? $0.row.state != "waiting" : $0.row.state == "waiting" }) : nil
        guard expanded, panel.isVisible,
              let rv = forced ?? rowViews.first(where: { $0.frame.contains(p) })
        else { hidePreview(); return }
        // Search mode: a chat match shows the whole matched line. A waiting agent: its question.
        // Any other row: after 0.35 s on it, "peek" = the last lines of the tab's screen.
        let chatLine = searchMode ? rv.note.flatMap { $0.hasPrefix("“") ? $0 : nil } : nil
        var body = chatLine ?? (rv.row.state == "waiting"
            ? rv.row.question ?? "Waiting for you (\(rv.row.detail ?? "no details"))." : nil)
        var mono = false
        if body == nil {
            guard let lines = peekLines(for: rv.row.session) else { return }  // asked; not here yet
            body = lines.isEmpty ? "(empty screen)" : lines.joined(separator: "\n")
            mono = true
        }
        let key = rv.row.session + body!
        if key == previewKey && preview.isVisible { return }
        previewKey = key
        let s = NSMutableAttributedString(attributedString: text(rv.row.name + "\n", .labelColor, bold: true))
        s.append(NSAttributedString(string: body!, attributes: [
            .font: mono ? NSFont.monospacedSystemFont(ofSize: fs(11), weight: .regular) : NSFont.systemFont(ofSize: fs(12)),
            .foregroundColor: NSColor.labelColor]))
        previewText.attributedStringValue = s
        let maxW: CGFloat = mono ? fs(560) : fs(400)
        previewText.preferredMaxLayoutWidth = maxW
        let fit = previewText.fittingSize
        let size = NSSize(width: min(maxW, fit.width) + pad * 2 + 4, height: min(fs(340), fit.height) + pad * 2)
        let rowTop = min(panel.frame.maxY, panel.convertToScreen(listView.convert(rv.frame, to: nil)).maxY)
        let vis = (panel.screen ?? NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        var x = panel.frame.minX - 6 - size.width
        if x < vis.minX { x = panel.frame.maxX + 6 }
        let y = max(vis.minY, rowTop - size.height)
        preview.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: false)
        previewText.frame = NSRect(x: pad + 2, y: pad, width: size.width - pad * 2 - 4, height: size.height - pad * 2)
        preview.orderFrontRegardless()
    }

    /// Peek: the hovered row's last screen lines from the daemon. nil while waiting: the request
    /// goes out after 0.35 s on the same row, and the reply (peek.json) comes within about 0.3 s.
    var hoverRow: (session: String, since: Date)?
    var peekAsked: (session: String, at: Date)?

    func peekLines(for session: String) -> [String]? {
        let now = Date()
        if hoverRow?.session != session {
            hoverRow = (session, now)
            if preview.isVisible { hidePreview(); hoverRow = (session, now) }
            return nil
        }
        if now.timeIntervalSince(hoverRow!.since) < 0.35 && !previewForced { return nil }
        if peekAsked?.session != session || now.timeIntervalSince(peekAsked!.at) > 5 {
            peekAsked = (session, now)  // ask again after 5 s, so a long hover shows new output
            sendRequest("peek", session)
        }
        guard let data = try? Data(contentsOf: peekFile),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              obj["session"] as? String == session,
              let ts = obj["ts"] as? Double, ts >= peekAsked!.at.timeIntervalSince1970 - 6,
              let lines = obj["lines"] as? [String] else { return nil }
        return lines.map { line in
            // A full-width divider (box-drawing characters only) would wrap: show it short.
            if !line.isEmpty, line.unicodeScalars.allSatisfy({ (0x2500...0x257F).contains($0.value) || $0 == " " }) {
                return String(repeating: "─", count: 24)
            }
            return line.count > 120 ? String(line.prefix(119)) + "…" : line
        }
    }

    func hidePreview() {
        hoverRow = nil
        previewKey = nil
        if preview.isVisible { preview.orderOut(nil) }
    }

    // AGENT_HOVER_PANEL_PREVIEW=1 (with AGENT_HOVER_PANEL_EXPANDED=1): preview the first waiting row
    // without the mouse, for screenshots.
    // AGENT_HOVER_PANEL_PREVIEW=peek: peek the first row that is not waiting.
    let previewForced = ["1", "peek"].contains(ProcessInfo.processInfo.environment["AGENT_HOVER_PANEL_PREVIEW"] ?? "")
    let forcedPeek = ProcessInfo.processInfo.environment["AGENT_HOVER_PANEL_PREVIEW"] == "peek"

    func setUpSearch() {
        searchIcon.image = NSImage(systemSymbolName: "magnifyingglass", accessibilityDescription: "search")
        searchIcon.isBordered = false
        searchIcon.contentTintColor = infoColor
        searchIcon.toolTip = "Search agent tabs (name, folder, chat)"
        searchIcon.target = self
        searchIcon.action = #selector(searchIconClicked)
        searchField.placeholderString = "name or folder"
        searchField.delegate = self
        searchField.font = NSFont.systemFont(ofSize: fs(12))
        searchField.focusRingType = .none
        chatToggle.target = self
        chatToggle.action = #selector(chatToggled)
        chatToggle.font = NSFont.systemFont(ofSize: fs(11))
        chatToggle.toolTip = "Also search the chat text (slower)"
        chatToggle.isEnabled = FileManager.default.isExecutableFile(atPath: searchPython.path)
        searchStatus.font = NSFont.monospacedDigitSystemFont(ofSize: fs(11), weight: .regular)
        searchStatus.textColor = infoColor
        searchStatus.alignment = .right
        for v in [searchIcon, searchField, chatToggle, searchStatus] as [NSView] {
            v.isHidden = true
            root.addSubview(v)
        }
        // A click outside the panel takes the keyboard away: end the search.
        NotificationCenter.default.addObserver(forName: NSWindow.didResignKeyNotification, object: panel,
                                               queue: .main) { [weak self] _ in
            guard let self = self, self.searchMode else { return }
            self.endSearch()
            if !self.panel.frame.contains(NSEvent.mouseLocation) { self.collapse() }
        }
    }

    // The project folder: the panel binary is <root>/.build/agent-hover-panel (AGENT_HOVER_ROOT overrides).
    lazy var projectRoot: URL = {
        if let r = ProcessInfo.processInfo.environment["AGENT_HOVER_ROOT"], !r.isEmpty {
            return URL(fileURLWithPath: r)
        }
        let exe = (Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])).resolvingSymlinksInPath()
        return exe.deletingLastPathComponent().deletingLastPathComponent()
    }()
    var searchPython: URL { projectRoot.appendingPathComponent(".venv/bin/python") }

    @objc func searchIconClicked() { searchMode ? endSearch() : startSearch() }

    func startSearch(query: String = "") {
        searchMode = true
        expanded = true
        panel.allowKey = true
        searchField.stringValue = query
        layout()
        panel.makeKey()
        panel.makeFirstResponder(searchField)
        if watchTimer == nil {
            watchTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                self?.checkOutside()
            }
        }
        scheduleChat()
    }

    func endSearch() {
        searchMode = false
        searchField.stringValue = ""
        chatToggle.state = .off
        chatResults = [:]
        chatQuery = ""
        chatTimer?.invalidate()
        chatProcess?.terminate()
        chatProcess = nil
        chatBusy = false
        panel.allowKey = false
        if panel.isKeyWindow {  // give the keyboard back to the app in front (iTerm2)
            panel.orderOut(nil)
            panel.orderFrontRegardless()
        }
        layout()
    }

    var query: String { searchField.stringValue.trimmingCharacters(in: .whitespaces) }

    func controlTextDidChange(_ obj: Notification) {
        layout()
        scheduleChat()
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        if selector == #selector(NSResponder.cancelOperation(_:)) {  // Esc
            collapse()
            return true
        }
        if selector == #selector(NSResponder.insertNewline(_:)) {  // Enter: raise the first row
            if let first = rowViews.first { raise(first.row) }
            return true
        }
        return false
    }

    @objc func chatToggled() {
        chatResults = [:]
        chatQuery = ""
        layout()
        scheduleChat()
    }

    /// Chat search runs agent_hover/search.py (0.1–1 s), 0.4 s after the last key.
    func scheduleChat() {
        chatTimer?.invalidate()
        guard searchMode, chatToggle.state == .on, query.count >= 2, query != chatQuery else { return }
        chatTimer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: false) { [weak self] _ in self?.runChat() }
    }

    func runChat() {
        let q = query
        chatProcess?.terminate()
        let p = Process()
        p.executableURL = searchPython
        p.arguments = ["-m", "agent_hover.search", "--chat", "--json", q]
        p.currentDirectoryURL = projectRoot
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        chatProcess = p
        chatBusy = true
        layout()
        DispatchQueue.global().async { [weak self] in
            try? p.run()
            let data = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            DispatchQueue.main.async {
                guard let self = self, self.chatProcess === p else { return }
                self.chatBusy = false
                self.chatProcess = nil
                var found: [String: String?] = [:]
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let results = obj["results"] as? [[String: Any]] {
                    for r in results where (r["where"] as? [String])?.contains("chat") == true {
                        if let s = r["session"] as? String { found[s] = r["snippet"] as? String }
                    }
                }
                self.chatResults = found
                self.chatQuery = q
                self.layout()
            }
        }
    }

    /// Search mode: keep the rows that match, each with a note on what matched.
    func filtered(_ rows: [Row]) -> [(Row, String?)] {
        guard searchMode, !query.isEmpty else { return rows.map { ($0, nil) } }
        let useChat = chatToggle.state == .on && chatQuery == query
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return rows.compactMap { r in
            let cwd = r.cwd ?? ""
            if useChat, let hit = chatResults[r.session] {
                return (r, hit.map { "“\($0)”" } ?? "chat")
            }
            if r.name.localizedCaseInsensitiveContains(query) { return (r, nil) }
            if cwd.localizedCaseInsensitiveContains(query) {
                return (r, cwd.hasPrefix(home) ? "~" + cwd.dropFirst(home.count) : cwd)
            }
            return nil
        }
    }

    // AGENT_HOVER_PANEL_SEARCH=<text> (with AGENT_HOVER_PANEL_EXPANDED=1): open search mode with this
    // text, for screenshots; AGENT_HOVER_PANEL_SEARCH_CHAT=1 also turns on the chat switch.
    func applyForcedSearch() {
        guard alwaysExpanded, let q = ProcessInfo.processInfo.environment["AGENT_HOVER_PANEL_SEARCH"] else { return }
        if ProcessInfo.processInfo.environment["AGENT_HOVER_PANEL_SEARCH_CHAT"] == "1" { chatToggle.state = .on }
        startSearch(query: q)
    }

    func refresh() {
        if let data = try? Data(contentsOf: panelFile) {
            snapshot = try? JSONDecoder().decode(Snapshot.self, from: data)
        }
        reloadAgentMarks()
        if reloadPanelConfig() {
            for v in [searchField, chatToggle, searchStatus] as [NSControl] {
                v.font = v === searchField ? NSFont.systemFont(ofSize: fs(12)) : NSFont.systemFont(ofSize: fs(11))
            }
            searchStatus.font = NSFont.monospacedDigitSystemFont(ofSize: fs(11), weight: .regular)
        }
        layout()
        syncSpace()
    }

    /// On the first desktop Space: order the panel in (it joins that Space). Elsewhere: order it
    /// out, so a panel started on another Space, or left on one, never shows there.
    func syncSpace() {
        let screen = NSScreen.screens.first { $0.frame.contains(NSPoint(x: anchor.x - 1, y: anchor.y - 1)) }
        let show = onFirstSpace(of: screen) ?? true
        if show && (!panel.isVisible || !panel.isOnActiveSpace) {
            panel.orderFrontRegardless()
        } else if !show && panel.isVisible {
            if expanded { collapse() }
            panel.orderOut(nil)
        }
    }

    /// The daemon wrote panel.json in the last 5 s.
    var fresh: Bool {
        guard let snap = snapshot else { return false }
        return Date().timeIntervalSince1970 - snap.ts < 5
    }

    func pillText() -> NSAttributedString {
        let out = NSMutableAttributedString()
        pillRow = nil
        guard let snap = snapshot, fresh else {
            out.append(text("○ agent-hover: daemon off", infoColor))
            return out
        }
        // The most recently active agent tab (latest hook event) comes first.
        if let r = snap.rows.max(by: { ($0.last ?? $0.since) < ($1.last ?? $1.since) }) {
            pillRow = r
            let name = r.name.count > 32 ? String(r.name.prefix(31)) + "…" : r.name
            var state = r.state
            if r.state == "waiting", let d = r.detail, !d.isEmpty { state += " (\(d))" }
            out.append(stateGlyph(r.state))
            out.append(text(" ", infoColor))
            out.append(text(name, r.buried ? dimColor : .labelColor, bold: true))
            out.append(text("  \(state) \(age(r.last ?? r.since))", infoColor, size: 11))
            out.append(text("     ", infoColor))
        }
        let visible = snap.rows.filter { !$0.buried }
        let counts: [(String, Int)] = [
            ("waiting", visible.filter { $0.state == "waiting" }.count),
            ("done", visible.filter { $0.state == "done" }.count),
            ("working", visible.filter { $0.state == "working" }.count),
        ]
        for (state, n) in counts where n > 0 {
            out.append(stateGlyph(state))
            out.append(text(" \(n)   ", mark(state).1, bold: true))
        }
        let buried = snap.rows.filter { $0.buried }.count
        if buried > 0 { out.append(text("▾ \(buried)", infoColor, bold: true)) }
        if out.length == 0 { out.append(text("○ \(snap.rows.count) agents", infoColor)) }
        if !snap.apply { out.append(text("  dry run", dimColor, size: 10)) }
        return out
    }

    func layout() {
        if docked && !expanded && !searchMode {
            layoutBar()
            return
        }
        barView.isHidden = true
        pill.isHidden = false
        root.layer?.cornerRadius = 9
        pill.content = pillText()
        let pillW = max(90, pill.content.size().width + pad * 2 + 6)
        rowViews.forEach { $0.removeFromSuperview() }
        rowViews = []
        let all = expanded && fresh ? sorted(rowsWithPendingPins(snapshot?.rows ?? [])) : []  // no stale list when the daemon is off
        let rows = filtered(all)
        let open = !all.isEmpty || searchMode
        let barH: CGFloat = searchMode ? fs(30) : 0
        let width = open ? max(pillW, listWidth) : pillW
        let screen = NSScreen.screens.first(where: { $0.frame.contains(NSPoint(x: anchor.x - 1, y: anchor.y - 1)) })
        let docH = rows.isEmpty ? 0 : CGFloat(rows.count) * rowHeight + 6
        // A long list stops at the screen's visible height; the rest scrolls.
        let maxListH = max(rowHeight * 3, (screen?.visibleFrame.height ?? 800) - pillHeight - barH - 16)
        let listH = rows.isEmpty ? (searchMode ? 4 : 0) : min(docH, maxListH)
        let height = pillHeight + barH + listH
        var frame = NSRect(x: anchor.x - width, y: anchor.y - height, width: width, height: height)
        if let screen = screen {
            frame.origin.x = max(screen.visibleFrame.minX, frame.origin.x)
            frame.origin.y = max(screen.visibleFrame.minY, frame.origin.y)
        }
        inLayout = true
        panel.setFrame(frame, display: false)
        inLayout = false
        root.frame = NSRect(origin: .zero, size: frame.size)
        pill.frame = NSRect(x: 0, y: height - pillHeight, width: width, height: pillHeight)
        searchIcon.isHidden = !open
        searchIcon.frame = NSRect(x: pad, y: height - pillHeight + fs(4), width: fs(18), height: fs(18))
        searchIcon.contentTintColor = searchMode ? .controlAccentColor : infoColor
        for v in [searchField, chatToggle, searchStatus] as [NSView] { v.isHidden = !searchMode }
        if searchMode {
            let barY = height - pillHeight - barH + 3
            searchStatus.stringValue = chatBusy ? "searching…" : query.isEmpty ? "" : "\(rows.count) found"
            searchStatus.frame = NSRect(x: width - pad - fs(74), y: barY + 3, width: fs(74), height: fs(16))
            chatToggle.frame = NSRect(x: width - pad - fs(130), y: barY + 1, width: fs(56), height: fs(20))
            searchField.frame = NSRect(x: pad, y: barY, width: width - pad * 3 - fs(130), height: fs(24))
        }
        scroll.isHidden = rows.isEmpty
        scroll.frame = NSRect(x: 0, y: 0, width: width, height: listH)
        listView.frame = NSRect(x: 0, y: 0, width: width, height: max(docH, listH))
        var y: CGFloat = 2
        for (r, note) in rows {
            let v = RowView(row: r, frame: NSRect(x: 0, y: y, width: width, height: rowHeight), note: note) {
                [weak self] row in self?.raise(row)
            }
            v.onPin = { [weak self] row, on in self?.setPin(row, on) }
            listView.addSubview(v)
            rowViews.append(v)
            y += rowHeight
        }
        pill.needsDisplay = true
        root.needsDisplay = true
    }

    /// Edge bar: a thin rounded bar at the right edge, as tall as the pill and two rows.
    func layoutBar() {
        rowViews.forEach { $0.removeFromSuperview() }
        rowViews = []
        let vis = (NSScreen.screens.first(where: { $0.frame.contains(NSPoint(x: anchor.x - 1, y: anchor.y - 1)) })
                   ?? NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        let h = pillHeight + rowHeight * 2
        let frame = NSRect(x: vis.maxX - barW, y: max(vis.minY, anchor.y - h), width: barW, height: h)
        inLayout = true
        panel.setFrame(frame, display: false)
        inLayout = false
        root.frame = NSRect(origin: .zero, size: frame.size)
        root.layer?.cornerRadius = barW / 2
        for v in [pill, searchIcon, searchField, chatToggle, searchStatus, scroll] as [NSView] { v.isHidden = true }
        barView.isHidden = false
        barView.frame = root.bounds
        barView.layer?.backgroundColor = barColor().cgColor
        pillRow = nil
    }

    func raise(_ row: Row) {
        sendRequest("raise", row.session)
        collapse()
    }

    /// Pin or unpin; the daemon keeps the pins. The list sorts again, pinned rows first.
    func setPin(_ row: Row, _ on: Bool) {
        sendRequest(on ? "pin" : "unpin", row.session)
        pendingPins[row.session] = (on, Date())
        order = [:]
        layout()
    }

    /// The daemon's rows with the pins not yet in panel.json applied.
    func rowsWithPendingPins(_ rows: [Row]) -> [Row] {
        pendingPins = pendingPins.filter { sid, p in
            Date().timeIntervalSince(p.since) < 5
                && rows.first(where: { $0.session == sid }).map { ($0.pinned == true) != p.on } ?? false
        }
        return rows.map { r in
            var r = r
            if let p = pendingPins[r.session] { r.pinned = p.on }
            return r
        }
    }

    func sendRequest(_ action: String, _ session: String) {
        try? FileManager.default.createDirectory(at: requestDir, withIntermediateDirectories: true)
        let id = UUID().uuidString
        let body = "{\"action\": \"\(action)\", \"session\": \"\(session)\", \"ts\": \(Date().timeIntervalSince1970)}"
        let tmp = requestDir.appendingPathComponent(".\(id).tmp")
        try? body.data(using: .utf8)?.write(to: tmp)
        try? FileManager.default.moveItem(at: tmp, to: requestDir.appendingPathComponent("\(id).json"))
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = Controller()
app.run()
