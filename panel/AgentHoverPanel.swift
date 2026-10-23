// agent-hover hover panel (POC 2).
//
// A small pill in the top-right corner (draggable) shows counts of agent tabs.
// Hover (the cursor rests on it for panel.hover_ms): it expands to a list of all agent tabs,
// most recently active first (buried ones are dimmed and marked ▾). The order is sorted when
// the list opens and stays while it is open.
// Each row has an agent mark (built-in table, overridden by ~/.agent-hover/agents.json). With the
// cursor on that mark, the hover frame shows the agent, model, CPU and memory instead of the screen;
// a red flame marks an agent that is not working but uses much CPU.
// Edge bar: drag the pill past the right edge of the screen (by panel.dock_px) and hold it there for
// panel.dock_delay_ms (an accent border then shows that a release docks it); release: the panel
// becomes a thin bar in the color of the most urgent state (waiting > done > working). Hover the bar
// for the full panel; drag the bar or the pill away from the edge to undock.
// Magnet: a dragged panel within panel.magnet_px of a screen edge snaps flush to it (also a panel
// just pulled out of the bar). Hold ⌥ (Option) while dragging: no magnet.
// Search: the magnifier on the open list starts search mode: the panel takes the keyboard and
// filters the rows by tab name and agent folder; the "chat" switch also searches the chat text
// (agent_hover/search.py). Recently closed agents (panel.closed_days) that match are listed under
// "Closed"; a click opens a new tab in its folder with the resume command typed (not run). Esc, a click outside or a click on a row ends it;
// Enter raises the first row.
// Click a row: ask the daemon to raise that tab (unbury, select, bring iTerm2 front).
// Hotkey (panel.hotkey, default ctrl+opt+space): open the list in search mode from anywhere.
// Stale tabs (done or idle for more than panel.fold_idle_h hours) fold into one line at the end.
//
// Data: reads ~/.agent-hover/panel.json (written by the daemon each second).
// Clicks: writes ~/.agent-hover/requests/<uuid>.json (the daemon handles it within 0.2 s).
// The panel never takes keyboard focus (non-activating), and has no Dock icon.
//
// Build: swiftc -O -swift-version 5 panel/AgentHoverPanel.swift -o .build/agent-hover-panel

import AppKit
import Carbon.HIToolbox

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
    let cpu: Double?       // % CPU of the agent's process tree (agent, MCP servers, its commands)
    let mem_mb: Double?    // memory of that tree, MB
    let hot: Bool?         // not working, but high CPU for minutes (daemon cpu_hot_pct / cpu_hot_min)
}

struct Snapshot: Decodable {
    let ts: Double
    let apply: Bool
    let update: Bool?  // the opt-in update check found a newer version (./run.sh update)
    let rows: [Row]
}

// Sizes follow config.json "panel": {"font_size": 12, "list_width": 440}; 12 is the base size.
var fontSize: CGFloat = 12
var listWidth: CGFloat = 440
var hoverMs: Double = 120  // hover intent: the cursor must rest this long before the list opens
var hotkeySpec = "ctrl+opt+space"  // "" or "off": no hotkey
var foldIdleH: Double = 12  // 0: never fold
var foldWaitingH: Double = 24  // a question unanswered this long also folds; 0: never
var closedDays: Double = 7  // search also lists agents closed in the last N days; 0: off
var dockPx: CGFloat = 4  // the dock zone: the drag pushes the panel this far past the screen's right edge
var dockDelayMs: Double = 500  // a drag must stay in the dock zone this long before a release docks
var magnetPx: CGFloat = 24  // a dragged panel this close to a screen edge snaps flush to it; 0: off
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
        hoverMs = min(1000, max(0, panelCfg["hover_ms"] as? Double ?? 120))
        hotkeySpec = panelCfg["hotkey"] as? String ?? "ctrl+opt+space"
        foldIdleH = max(0, panelCfg["fold_idle_h"] as? Double ?? 12)
        foldWaitingH = max(0, panelCfg["fold_waiting_h"] as? Double ?? 24)
        closedDays = max(0, panelCfg["closed_days"] as? Double ?? 7)
        dockPx = CGFloat(min(200, max(0, panelCfg["dock_px"] as? Double ?? 4)))
        dockDelayMs = min(5000, max(0, panelCfg["dock_delay_ms"] as? Double ?? 500))
        magnetPx = CGFloat(min(200, max(0, panelCfg["magnet_px"] as? Double ?? 24)))
    } else {
        hoverMs = 120
        hotkeySpec = "ctrl+opt+space"
        foldIdleH = 12
        foldWaitingH = 24
        closedDays = 7
        dockPx = 4
        dockDelayMs = 500
        magnetPx = 24
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
    var onDragBegin: (() -> Void)?
    var onDragMove: ((NSPoint) -> NSPoint)?
    var onDragEnd: (() -> Void)?
    var onClick: (() -> Void)?

    override func draw(_ dirtyRect: NSRect) {
        let size = content.size()
        content.draw(at: NSPoint(x: bounds.maxX - size.width - pad - 2, y: (bounds.height - size.height) / 2))
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        if let win = window {
            dragLoop(win, onBegin: onDragBegin, onMove: onDragMove, onEnd: onDragEnd, onClick: onClick)
        }
    }
}

/// Own drag loop (not performDrag), so a click and a drag can be told apart. The 1 s refresh
/// timer does not run in this loop, so it cannot move the panel back during the drag. The loop
/// also wakes every 50 ms with no mouse event, so onMove can time how long the panel rests.
/// onMove gets the raw position (where the cursor drags the panel) and returns where to put it
/// (the magnet), so the panel moves once per tick, with no jitter.
/// onBegin may resize the window (the edge bar turns into the pill): the move goes on from there.
func dragLoop(_ win: NSWindow, onBegin: (() -> Void)?, onMove: ((NSPoint) -> NSPoint)?, onEnd: (() -> Void)?,
              onClick: (() -> Void)?) {
    var start = NSEvent.mouseLocation
    var origin = win.frame.origin
    var moved = false
    while true {
        let e = win.nextEvent(matching: [.leftMouseDragged, .leftMouseUp], until: Date(timeIntervalSinceNow: 0.05),
                              inMode: .eventTracking, dequeue: true)
        let p = NSEvent.mouseLocation
        if !moved && hypot(p.x - start.x, p.y - start.y) > 3 {
            moved = true
            onBegin?()
            if win.frame.origin != origin {  // resized by onBegin: keep the cursor's place on the panel
                origin = NSPoint(x: p.x - win.frame.width + 12, y: p.y - win.frame.height + 8)
                start = p
            }
        }
        if moved {
            let raw = NSPoint(x: origin.x + p.x - start.x, y: origin.y + p.y - start.y)
            let to = onMove?(raw) ?? raw
            if to != win.frame.origin { win.setFrameOrigin(to) }
        }
        if e?.type == .leftMouseUp { break }
    }
    if moved { onEnd?() } else { onClick?() }
}

/// The edge bar. A drag pulls the panel out (as the pill); see Controller.dragMoved for the rule.
final class BarView: NSView {
    var onDragBegin: (() -> Void)?
    var onDragMove: ((NSPoint) -> NSPoint)?
    var onDragEnd: (() -> Void)?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        if let win = window { dragLoop(win, onBegin: onDragBegin, onMove: onDragMove, onEnd: onDragEnd, onClick: nil) }
    }
}

/// A recently closed agent from the search (agent_hover/search.py --closed).
struct ClosedHit {
    let name: String
    let agent: String
    let agentSession: String
    let cwd: String
    let last: Double
    let resume: String
    let note: String?  // the chat snippet, or the folder when only the folder matched
}

/// One closed agent in the search results, dimmed. A click resumes it in a new tab (Controller.resume).
final class ClosedRowView: NSView {
    let hit: ClosedHit
    let onClick: (ClosedHit) -> Void
    private var hover = false

    init(hit: ClosedHit, frame: NSRect, onClick: @escaping (ClosedHit) -> Void) {
        self.hit = hit
        self.onClick = onClick
        super.init(frame: frame)
        toolTip = "Click: a new tab in its folder, with this command typed (press Enter to run it)\n\(hit.resume)"
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    required init?(coder: NSCoder) { fatalError() }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseEntered(with event: NSEvent) { hover = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hover = false; needsDisplay = true }
    override func mouseDown(with event: NSEvent) { onClick(hit) }

    override func draw(_ dirtyRect: NSRect) {
        if hover {
            NSColor.selectedContentBackgroundColor.withAlphaComponent(0.35).setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 4, dy: 1), xRadius: 5, yRadius: 5).fill()
        }
        let y = (bounds.height - fs(15)) / 2
        drawCentered(text("○", dimColor), centerX: pad + fs(20), y: y)
        let ageText = text(age(hit.last), dimColor, size: 11)
        ageText.draw(at: NSPoint(x: bounds.width - pad - ageText.size().width, y: y + 1))
        drawCentered(agentBadge(hit.agent), centerX: bounds.width - pad - ageColumnW - 6 - markColumnW / 2, y: y + 1)
        let rightW = ageColumnW + 6 + markColumnW
        let style = NSMutableParagraphStyle()
        style.lineBreakMode = .byTruncatingTail
        let s = NSMutableAttributedString(string: hit.name, attributes: [
            .font: NSFont.systemFont(ofSize: fs(12)), .foregroundColor: dimColor, .paragraphStyle: style,
        ])
        if let note = hit.note {
            s.append(NSAttributedString(string: "  \(note)", attributes: [
                .font: NSFont.systemFont(ofSize: fs(11)), .foregroundColor: infoColor, .paragraphStyle: style,
            ]))
        }
        s.draw(in: NSRect(x: pad + fs(32), y: y, width: bounds.width - rightW - pad * 2 - fs(40), height: fs(16)))
    }
}

/// The "Closed" heading above the closed agents in the search results.
final class HeadingView: NSView {
    let label: String
    init(label: String, frame: NSRect) {
        self.label = label
        super.init(frame: frame)
    }
    required init?(coder: NSCoder) { fatalError() }
    override func draw(_ dirtyRect: NSRect) {
        let t = text(label, infoColor, bold: true, size: 11)
        t.draw(at: NSPoint(x: pad + fs(14), y: (bounds.height - t.size().height) / 2))
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

/// The hover frame's text when the cursor is on a row's agent mark (or its flame): agent, model,
/// CPU and memory. Not a tooltip: the rows are rebuilt each second, and the panel never takes
/// focus, so macOS tooltips do not show.
func rowInfo(_ row: Row) -> String {
    var lines = ["Agent: \(row.agent)"]
    if let m = row.model { lines.append("Model: \(m)") }
    if let c = row.cpu { lines.append("CPU: \(Int(c.rounded()))% (with its MCP servers and commands)") }
    if let mb = row.mem_mb { lines.append("Memory: \(Int(mb)) MB") }
    if row.hot == true { lines.append("High CPU while not working: stuck, or a command still running") }
    return lines.joined(separator: "\n")
}

/// The x range (row coordinates) of a row's agent mark, and of the flame left of it when shown.
func markHitRange(_ row: Row, width: CGFloat) -> ClosedRange<CGFloat> {
    let markX = width - pad - ageColumnW - 6 - markColumnW / 2
    let flameW = row.hot == true ? (flameIcon()?.size.width ?? 0) + 4 : 0
    return (markX - markColumnW / 2 - flameW)...(markX + markColumnW / 2)
}

/// The high-CPU mark (SF Symbol, tinted).
func flameIcon() -> NSImage? {
    NSImage(systemSymbolName: "flame.fill", accessibilityDescription: "high CPU")?
        .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: fs(10), weight: .bold)
            .applying(NSImage.SymbolConfiguration(paletteColors: [.systemRed])))
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
        var rightW = ageColumnW + 6 + markColumnW
        if row.hot == true, let flame = flameIcon() {  // left of the agent mark
            let x = markX - markColumnW / 2 - flame.size.width - 2
            flame.draw(in: NSRect(x: x, y: (bounds.height - flame.size.height) / 2, width: flame.size.width,
                                  height: flame.size.height))
            rightW += flame.size.width + 4
        }
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

/// A global hotkey. Carbon's RegisterEventHotKey needs no Accessibility permission.
final class HotKey {
    static var action: (() -> Void)?
    private static var handlerInstalled = false
    private var ref: EventHotKeyRef?

    static let keyCodes: [String: Int] = [
        "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D, "e": kVK_ANSI_E, "f": kVK_ANSI_F,
        "g": kVK_ANSI_G, "h": kVK_ANSI_H, "i": kVK_ANSI_I, "j": kVK_ANSI_J, "k": kVK_ANSI_K, "l": kVK_ANSI_L,
        "m": kVK_ANSI_M, "n": kVK_ANSI_N, "o": kVK_ANSI_O, "p": kVK_ANSI_P, "q": kVK_ANSI_Q, "r": kVK_ANSI_R,
        "s": kVK_ANSI_S, "t": kVK_ANSI_T, "u": kVK_ANSI_U, "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X,
        "y": kVK_ANSI_Y, "z": kVK_ANSI_Z,
        "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3, "4": kVK_ANSI_4,
        "5": kVK_ANSI_5, "6": kVK_ANSI_6, "7": kVK_ANSI_7, "8": kVK_ANSI_8, "9": kVK_ANSI_9,
        "space": kVK_Space, "return": kVK_Return, "enter": kVK_Return, "tab": kVK_Tab,
        "`": kVK_ANSI_Grave, "-": kVK_ANSI_Minus, "=": kVK_ANSI_Equal, "/": kVK_ANSI_Slash,
        ".": kVK_ANSI_Period, ",": kVK_ANSI_Comma, ";": kVK_ANSI_Semicolon, "'": kVK_ANSI_Quote,
        "[": kVK_ANSI_LeftBracket, "]": kVK_ANSI_RightBracket, "\\": kVK_ANSI_Backslash,
        "f1": kVK_F1, "f2": kVK_F2, "f3": kVK_F3, "f4": kVK_F4, "f5": kVK_F5, "f6": kVK_F6,
        "f7": kVK_F7, "f8": kVK_F8, "f9": kVK_F9, "f10": kVK_F10, "f11": kVK_F11, "f12": kVK_F12,
    ]
    static let modifiers: [String: Int] = [
        "ctrl": controlKey, "control": controlKey, "opt": optionKey, "option": optionKey, "alt": optionKey,
        "cmd": cmdKey, "command": cmdKey, "shift": shiftKey,
    ]

    /// "ctrl+opt+space" -> (key code, Carbon modifiers). nil if not understood, or if a plain
    /// key (not F1–F12) has no modifier: that would take the key from every app.
    static func parse(_ spec: String) -> (UInt32, UInt32)? {
        let parts = spec.lowercased().split(separator: "+").map { $0.trimmingCharacters(in: .whitespaces) }
        guard let keyName = parts.last, let code = keyCodes[keyName] else { return nil }
        var mods = 0
        for m in parts.dropLast() {
            guard let v = modifiers[m] else { return nil }
            mods |= v
        }
        if mods == 0 && !keyName.hasPrefix("f") { return nil }
        return (UInt32(code), UInt32(mods))
    }

    /// Register spec (replacing the old hotkey). false if the text is not understood or the
    /// system refuses it.
    func register(_ spec: String) -> Bool {
        unregister()
        guard let (code, mods) = HotKey.parse(spec) else { return false }
        if !HotKey.handlerInstalled {
            var type = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
            InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
                DispatchQueue.main.async { HotKey.action?() }
                return noErr
            }, 1, &type, nil, nil)
            HotKey.handlerInstalled = true
        }
        let id = EventHotKeyID(signature: OSType(0x41484B31), id: 1)
        return RegisterEventHotKey(code, mods, id, GetApplicationEventTarget(), 0, &ref) == noErr
    }

    func unregister() {
        if let r = ref { UnregisterEventHotKey(r) }
        ref = nil
    }
}

/// The last line of the list: "▸ 5 stale (idle > 12 h, waiting > 24 h)". Hover (the cursor rests on it for
/// hover_ms; Controller.checkFoldHover) shows those rows; a click shows or hides them.
final class FoldView: NSView {
    let count: Int
    let open: Bool
    let onClick: () -> Void
    private var hover = false

    init(count: Int, open: Bool, frame: NSRect, onClick: @escaping () -> Void) {
        self.count = count
        self.open = open
        self.onClick = onClick
        super.init(frame: frame)
        addTrackingArea(NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                                       owner: self, userInfo: nil))
    }

    required init?(coder: NSCoder) { fatalError() }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseEntered(with event: NSEvent) { hover = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hover = false; needsDisplay = true }
    override func mouseDown(with event: NSEvent) { onClick() }

    override func draw(_ dirtyRect: NSRect) {
        if hover {
            NSColor.selectedContentBackgroundColor.withAlphaComponent(0.35).setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 4, dy: 1), xRadius: 5, yRadius: 5).fill()
        }
        func h(_ v: Double) -> String { v == v.rounded() ? "\(Int(v)) h" : "\(v) h" }
        let why = [foldIdleH > 0 ? "idle > \(h(foldIdleH))" : nil, foldWaitingH > 0 ? "waiting > \(h(foldWaitingH))" : nil]
            .compactMap { $0 }.joined(separator: ", ")
        let label = text("\(open ? "▾" : "▸")  \(count) stale (\(why))", infoColor, size: 11)
        label.draw(at: NSPoint(x: pad + fs(14), y: (bounds.height - label.size().height) / 2))
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
    var animating = false  // the dock animation moves the panel: do not save those positions
    var intentTimer: Timer?
    var intentStart = Date()
    var intentSamples: [(t: Date, p: NSPoint)] = []
    let hotKey = HotKey()
    var hotkeyRegistered: String?  // the spec now registered (nil: none yet)
    var foldOpen = false  // the stale rows are shown (until the list closes)
    var foldView: FoldView?
    var foldHoverSince: Date?
    var foldHoverBlocked = false  // a click closed the fold: no hover-open until the cursor leaves it
    var hoverBlocked = false  // just docked under the cursor: do not open until the cursor leaves the bar
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
    var closedResults: [ClosedHit] = []  // recently closed agents that match chatQuery
    var closedViews: [NSView] = []
    var statusNote: (text: String, since: Date)?  // a short message in the search bar ("copied")
    var dockArmed = false  // during a drag: a release now docks the panel
    var zoneSince: Date?   // during a drag: when the panel came into the dock zone
    var chatQuery = ""
    var chatBusy = false
    var chatProcess: Process?
    var chatTimer: Timer?

    var anchor = NSPoint.zero  // top-right corner of the panel, in screen coordinates
    var docked = false         // edge bar mode: at the right edge of the screen
    let barView = BarView()    // the thin bar, shown when docked and not expanded
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
        pill.onDragBegin = { [weak self] in self?.dragBegan() }
        pill.onDragMove = { [weak self] raw in self?.dragMoved(raw) ?? raw }
        pill.onDragEnd = { [weak self] in self?.dragEnded() }
        barView.onDragBegin = { [weak self] in self?.barDragBegan() }
        barView.onDragMove = { [weak self] raw in self?.dragMoved(raw) ?? raw }
        barView.onDragEnd = { [weak self] in self?.dragEnded() }
        pill.onClick = { [weak self] in
            if let self = self, let r = self.pillRow { self.raise(r) }
        }
        // performDrag can return before the move ends, so also save on each window move.
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: panel, queue: .main) {
            [weak self] _ in
            guard let self = self, !self.inLayout, !self.animating else { return }
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
        HotKey.action = { [weak self] in self?.hotkeyPressed() }
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
        return NSPoint(x: f.maxX, y: f.maxY)  // the top-right corner, flush
    }

    /// The screen a drag is on: the one under the cursor (panel.screen lags when crossing screens).
    func dragScreen() -> NSScreen? {
        NSScreen.screens.first { $0.frame.contains(NSEvent.mouseLocation) } ?? panel.screen ?? NSScreen.main
    }

    /// Outer edges of a screen next to the frame: no other screen lies just beyond them, so the
    /// magnet and the dock zone apply there. An edge shared with another monitor is open.
    func outerEdges(_ screen: NSScreen, near f: NSRect) -> (left: Bool, right: Bool, bottom: Bool, top: Bool) {
        let s = screen.frame
        func free(_ p: NSPoint) -> Bool { !NSScreen.screens.contains { $0 !== screen && $0.frame.contains(p) } }
        let midY = min(max(f.midY, s.minY + 1), s.maxY - 1), midX = min(max(f.midX, s.minX + 1), s.maxX - 1)
        return (free(NSPoint(x: s.minX - 1, y: midY)), free(NSPoint(x: s.maxX + 1, y: midY)),
                free(NSPoint(x: midX, y: s.minY - 1)), free(NSPoint(x: midX, y: s.maxY + 1)))
    }

    /// Dock zone: the drag pushes the panel's right side at least dock_px past the screen's right
    /// edge (the raw position, before the magnet), and that edge is an outer one. A panel that only
    /// sits flush at the edge is not in it.
    func inDockZone(_ f: NSRect) -> Bool {
        guard let screen = dragScreen() else { return false }
        return outerEdges(screen, near: f).right && f.maxX >= screen.visibleFrame.maxX + dockPx
    }

    /// A drag of the pill starts. A docked panel starts armed: it stays docked unless pulled out.
    func dragBegan() {
        dockArmed = docked && inDockZone(panel.frame)
        zoneSince = dockArmed ? Date() : nil
        showArmed()
    }

    /// A drag of the bar starts: the bar turns into the pill under the cursor, still armed.
    func barDragBegan() {
        stopIntent()
        docked = false
        expanded = false
        layout()
        dockArmed = true
        zoneSince = Date()
        showArmed()
    }

    /// During a drag (also every 50 ms while the mouse rests): the panel must stay in the dock zone
    /// for dock_delay_ms before a release docks it; out of the zone, it is not armed at once.
    /// So docking needs a push past the edge and a short hold; a small pull back undocks (panel
    /// preferred).
    func dragMoved(_ raw: NSPoint) -> NSPoint {
        let f = NSRect(origin: raw, size: panel.frame.size)
        if inDockZone(f) {
            let now = Date()
            if zoneSince == nil { zoneSince = now }
            if now.timeIntervalSince(zoneSince!) >= dockDelayMs / 1000 { dockArmed = true }
        } else {
            zoneSince = nil
            dockArmed = false
        }
        showArmed()
        return magnetPx > 0 && !NSEvent.modifierFlags.contains(.option) ? snapped(f).origin : raw
    }

    /// Magnet: an edge of the panel within magnet_px of an outer screen edge (or past it) snaps
    /// flush to it. It works on the raw position, so the snap never adds up, and inDockZone reads
    /// the raw position too: a push past the edge still docks. At an edge shared with another
    /// monitor there is no snap, so the panel can move across.
    func snapped(_ raw: NSRect) -> NSRect {
        guard let screen = dragScreen() else { return raw }
        let vis = screen.visibleFrame, outer = outerEdges(screen, near: raw)
        var f = raw
        if outer.right && vis.maxX - f.maxX < magnetPx { f.origin.x = vis.maxX - f.width }
        else if outer.left && f.minX - vis.minX < magnetPx { f.origin.x = vis.minX }
        if outer.bottom && f.minY - vis.minY < magnetPx { f.origin.y = vis.minY }
        if outer.top && vis.maxY - f.maxY < magnetPx { f.origin.y = vis.maxY - f.height }  // top wins
        return f
    }

    /// The accent border: a release now docks the panel.
    func showArmed() {
        root.layer?.borderWidth = dockArmed ? 2 : 0
        root.layer?.borderColor = NSColor.controlAccentColor.cgColor
    }

    /// A drag ended: armed, the panel shrinks into the edge bar at once (no wait for the cursor to
    /// leave); otherwise it stays where it was dropped, kept inside the screen.
    func dragEnded() {
        root.layer?.borderWidth = 0
        savePosition()
        docked = dockArmed
        dockArmed = false
        zoneSince = nil
        if let vis = (panel.screen ?? NSScreen.main)?.visibleFrame {
            anchor.x = docked ? vis.maxX : min(anchor.x, vis.maxX)
        }
        savePosition(keepAnchor: true)
        if !docked { layout(); return }
        if searchMode { endSearch() }
        hidePreview()
        watchTimer?.invalidate()
        watchTimer = nil
        expanded = alwaysExpanded
        order = [:]
        let from = panel.frame
        layout()
        let to = panel.frame
        hoverBlocked = to.contains(NSEvent.mouseLocation)
        // Slide into the edge, so the eye sees where the panel went.
        animating = true
        inLayout = true
        panel.setFrame(from, display: false)
        inLayout = false
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.15
            panel.animator().setFrame(to, display: true)
        }, completionHandler: { [weak self] in self?.animating = false })
    }

    /// The bar's color: the most urgent state among the shown tabs (stale ones do not count, so an
    /// old unanswered question does not keep the bar orange).
    func barColor() -> NSColor {
        guard let snap = snapshot, fresh else { return infoColor }
        let states = Set(snap.rows.filter { !$0.buried && !isStale($0) }.map { $0.state })
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
        if !inside { hoverBlocked = false }
        if alwaysExpanded || !inside || expanded || hoverBlocked { return }
        if hoverMs <= 0 { expand(); return }
        guard intentTimer == nil else { return }
        intentStart = Date()
        intentSamples = []
        intentTimer = Timer.scheduledTimer(withTimeInterval: 0.02, repeats: true) { [weak self] _ in
            self?.checkIntent()
        }
    }

    /// Hover intent: open when the cursor rests on the panel (moves less than 4 px in hover_ms),
    /// or stays on it for 3 × hover_ms. A cursor that only passes over (for example to a menu
    /// bar icon) does not open the list.
    func checkIntent() {
        let p = NSEvent.mouseLocation, now = Date()
        guard panel.frame.contains(p), !expanded, !hoverBlocked else { stopIntent(); return }
        let window = hoverMs / 1000
        intentSamples.append((now, p))
        intentSamples.removeAll { now.timeIntervalSince($0.t) > window }
        let inside = now.timeIntervalSince(intentStart)
        let rested = inside >= window && intentSamples.allSatisfy { hypot($0.p.x - p.x, $0.p.y - p.y) < 4 }
        if rested || inside >= window * 3 {
            stopIntent()
            expand()
        }
    }

    func stopIntent() {
        intentTimer?.invalidate()
        intentTimer = nil
    }

    func expand() {
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
        if panel.frame.contains(NSEvent.mouseLocation) { outsideSince = nil; checkFoldHover(); updatePreview(); return }
        hidePreview()
        if alwaysExpanded { return }
        let now = Date()
        if outsideSince == nil { outsideSince = now }
        if now.timeIntervalSince(outsideSince!) >= 0.4 { collapse() }
    }

    /// Polled (not a timer in the fold line): the list rebuilds each second and would drop it.
    func checkFoldHover() {
        // The same hit test as updatePreview: the cursor in list coordinates, inside the scroll view.
        let inWindow = panel.convertPoint(fromScreen: NSEvent.mouseLocation)
        let onList = scroll.bounds.contains(scroll.convert(inWindow, from: nil))
        let onFold = onList && foldView.map { $0.frame.contains(listView.convert(inWindow, from: nil)) } ?? false
        if !onFold { foldHoverBlocked = false }
        guard foldView != nil, !foldOpen, onFold, !foldHoverBlocked else {
            foldHoverSince = nil
            return
        }
        let now = Date()
        if foldHoverSince == nil { foldHoverSince = now }
        if now.timeIntervalSince(foldHoverSince!) >= max(0.15, hoverMs / 1000) {
            foldHoverSince = nil
            foldOpen = true
            layout()
        }
    }

    func collapse() {
        if searchMode { endSearch() }
        hidePreview()
        watchTimer?.invalidate()
        watchTimer = nil
        expanded = alwaysExpanded
        order = [:]
        foldOpen = false
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
        let onMark = forced == nil && markHitRange(rv.row, width: rv.frame.width).contains(p.x)
        let chatLine = searchMode ? rv.note.flatMap { $0.hasPrefix("“") ? $0 : nil } : nil
        var body = onMark ? rowInfo(rv.row) : chatLine ?? (rv.row.state == "waiting"
            ? rv.row.question ?? (rv.row.detail.flatMap { $0.isEmpty ? nil : "Waiting for you (\($0))." }
                                  ?? "Waiting for you.") : nil)
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
        closedResults = []
        statusNote = nil
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
        if query.count < 2 { closedResults = [] }
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
        guard searchMode, chatToggle.state == .on || closedDays > 0, query.count >= 2, query != chatQuery else { return }
        chatTimer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: false) { [weak self] _ in self?.runChat() }
    }

    func runChat() {
        let q = query
        chatProcess?.terminate()
        let p = Process()
        p.executableURL = searchPython
        p.arguments = ["-m", "agent_hover.search", "--json"] + (chatToggle.state == .on ? ["--chat"] : [])
            + (closedDays > 0 ? ["--closed"] : []) + [q]
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
                var closed: [ClosedHit] = []
                let home = FileManager.default.homeDirectoryForCurrentUser.path
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    for r in obj["results"] as? [[String: Any]] ?? []
                    where (r["where"] as? [String])?.contains("chat") == true {
                        if let s = r["session"] as? String { found[s] = r["snippet"] as? String }
                    }
                    for c in obj["closed"] as? [[String: Any]] ?? [] {
                        let cwd = c["cwd"] as? String ?? ""
                        let folder = cwd.hasPrefix(home) ? "~" + cwd.dropFirst(home.count) : cwd
                        let note = (c["snippet"] as? String).map { "“\($0)”" }
                            ?? ((c["where"] as? [String])?.contains("folder") == true ? folder : nil)
                        closed.append(ClosedHit(name: c["name"] as? String ?? "?", agent: c["agent"] as? String ?? "",
                                                agentSession: c["agent_session"] as? String ?? "",
                                                cwd: cwd, last: c["last"] as? Double ?? 0,
                                                resume: c["resume"] as? String ?? "", note: note))
                    }
                }
                self.chatResults = found
                self.closedResults = closed
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
        if hoverBlocked && !panel.frame.contains(NSEvent.mouseLocation) { hoverBlocked = false }  // lost exit event
        if let data = try? Data(contentsOf: panelFile) {
            snapshot = try? JSONDecoder().decode(Snapshot.self, from: data)
        }
        reloadAgentMarks()
        let layoutChanged = reloadPanelConfig()
        if hotkeySpec != hotkeyRegistered {
            hotkeyRegistered = hotkeySpec
            let off = ["", "off", "none"].contains(hotkeySpec.lowercased())
            if off { hotKey.unregister() }
            else if !hotKey.register(hotkeySpec) { FileHandle.standardError.write("hotkey not set: \(hotkeySpec)\n".data(using: .utf8)!) }
        }
        if layoutChanged {
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
        } else if !show && panel.isVisible && !searchMode {  // a search from the hotkey stays
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
        if snap.update == true { out.append(text("  ↑ update", .systemBlue, size: 10)) }
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
        foldView?.removeFromSuperview()
        foldView = nil
        closedViews.forEach { $0.removeFromSuperview() }
        closedViews = []
        let all = expanded && fresh ? sorted(rowsWithPendingPins(snapshot?.rows ?? [])) : []  // no stale list when the daemon is off
        var rows = filtered(all)
        // Search shows every match; otherwise stale rows go after one fold line.
        let stale = searchMode ? [] : rows.filter { isStale($0.0) }
        if !stale.isEmpty { rows = rows.filter { !isStale($0.0) } + (foldOpen ? stale : []) }
        let foldAt = rows.count - (foldOpen ? stale.count : 0)  // index of the fold line
        let open = !all.isEmpty || searchMode
        let barH: CGFloat = searchMode ? fs(30) : 0
        let width = open ? max(pillW, listWidth) : pillW
        let screen = NSScreen.screens.first(where: { $0.frame.contains(NSPoint(x: anchor.x - 1, y: anchor.y - 1)) })
        let closed = searchMode && chatQuery == query && query.count >= 2 ? closedResults : []
        let lines = rows.count + (stale.isEmpty ? 0 : 1) + (closed.isEmpty ? 0 : closed.count + 1)
        let docH = lines == 0 ? 0 : CGFloat(lines) * rowHeight + 6
        // A long list stops at the screen's visible height; the rest scrolls.
        let maxListH = max(rowHeight * 3, (screen?.visibleFrame.height ?? 800) - pillHeight - barH - 16)
        let listH = lines == 0 ? (searchMode ? 4 : 0) : min(docH, maxListH)
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
            if let n = statusNote, Date().timeIntervalSince(n.since) > 2.9 { statusNote = nil }
            searchStatus.stringValue = statusNote?.text ?? (chatBusy ? "searching…" : query.isEmpty ? ""
                : closed.isEmpty ? "\(rows.count) found" : "\(rows.count) + \(closed.count) closed")
            searchStatus.frame = NSRect(x: width - pad - fs(74), y: barY + 3, width: fs(74), height: fs(16))
            chatToggle.frame = NSRect(x: width - pad - fs(130), y: barY + 1, width: fs(56), height: fs(20))
            searchField.frame = NSRect(x: pad, y: barY, width: width - pad * 3 - fs(130), height: fs(24))
        }
        scroll.isHidden = lines == 0
        scroll.frame = NSRect(x: 0, y: 0, width: width, height: listH)
        listView.frame = NSRect(x: 0, y: 0, width: width, height: max(docH, listH))
        var y: CGFloat = 2
        func addFold() {
            let f = FoldView(count: stale.count, open: foldOpen, frame: NSRect(x: 0, y: y, width: width, height: rowHeight)) {
                [weak self] in
                guard let self = self else { return }
                self.foldOpen.toggle()
                self.foldHoverBlocked = !self.foldOpen  // closed by a click under the cursor
                self.layout()
            }
            listView.addSubview(f)
            foldView = f
            y += rowHeight
        }
        for (i, (r, note)) in rows.enumerated() {
            if !stale.isEmpty && i == foldAt { addFold() }
            let v = RowView(row: r, frame: NSRect(x: 0, y: y, width: width, height: rowHeight), note: note) {
                [weak self] row in self?.raise(row)
            }
            v.onPin = { [weak self] row, on in self?.setPin(row, on) }
            listView.addSubview(v)
            rowViews.append(v)
            y += rowHeight
        }
        if !stale.isEmpty && foldAt == rows.count { addFold() }
        if !closed.isEmpty {
            let days = closedDays == closedDays.rounded() ? "\(Int(closedDays))" : "\(closedDays)"
            let h = HeadingView(label: "Closed · last \(days) days · click: resume in a new tab",
                                frame: NSRect(x: 0, y: y, width: width, height: rowHeight))
            listView.addSubview(h)
            closedViews.append(h)
            y += rowHeight
            for hit in closed {
                let v = ClosedRowView(hit: hit, frame: NSRect(x: 0, y: y, width: width, height: rowHeight)) {
                    [weak self] hit in self?.resume(hit)
                }
                listView.addSubview(v)
                closedViews.append(v)
                y += rowHeight
            }
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

    /// A closed agent: the daemon opens a new tab, runs `cd <folder>` and types the resume
    /// command without Enter.
    func resume(_ hit: ClosedHit) {
        sendRequest("resume", "", extra: ["agent": hit.agent, "agent_session": hit.agentSession, "cwd": hit.cwd])
        collapse()
    }

    /// The hotkey: open the list in search mode, also on another Space; again: close it.
    func hotkeyPressed() {
        if searchMode { collapse(); return }
        if !panel.isVisible || !panel.isOnActiveSpace {
            panel.collectionBehavior = [.ignoresCycle, .moveToActiveSpace]
            panel.orderFrontRegardless()
            panel.collectionBehavior = [.ignoresCycle]
        }
        stopIntent()
        hoverBlocked = false
        startSearch()
    }

    /// Folded at the end (not pinned, not new): done or idle for more than fold_idle_h hours, or a
    /// question left unanswered for more than fold_waiting_h hours (an abandoned session).
    func isStale(_ r: Row) -> Bool {
        guard r.pinned != true && r.unseen != true else { return false }
        let hours = (Date().timeIntervalSince1970 - (r.last ?? r.since)) / 3600
        if r.state == "waiting" { return foldWaitingH > 0 && hours > foldWaitingH }
        return foldIdleH > 0 && ["done", "idle", "ended"].contains(r.state) && hours > foldIdleH
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

    func sendRequest(_ action: String, _ session: String, extra: [String: String] = [:]) {
        try? FileManager.default.createDirectory(at: requestDir, withIntermediateDirectories: true)
        let id = UUID().uuidString
        var obj: [String: Any] = extra
        obj["action"] = action
        obj["session"] = session
        obj["ts"] = Date().timeIntervalSince1970
        guard let body = try? JSONSerialization.data(withJSONObject: obj) else { return }
        let tmp = requestDir.appendingPathComponent(".\(id).tmp")
        try? body.write(to: tmp)
        try? FileManager.default.moveItem(at: tmp, to: requestDir.appendingPathComponent("\(id).json"))
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = Controller()
app.run()
