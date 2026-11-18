// MIT: optional Nabu controls hosted inside an upstream-supported display clone.
import QtQuick
import Quickshell.Io
import qs.Ui

Column {
  id: controls
  property bool panelOpened: false
  property color foreground: "white"
  property string fontFamily: "sans-serif"
  property var controlState: ({})
  property string failure: ""
  spacing: 4

  function refresh() {
    if (!stateProc.running && !actionProc.running) stateProc.running = true
  }

  function request(action) {
    if (actionProc.running) return
    failure = ""
    actionProc.command = ["omarchy-nabu-display-control", action]
    actionProc.running = true
  }

  function accept(text) {
    try {
      var value = JSON.parse(text)
      if (value.error) { failure = "Couldn't update display setting"; return }
      controlState = value
    } catch (error) {
      console.warn("Nabu display state:", error)
    }
  }

  onPanelOpenedChanged: if (panelOpened) refresh()
  Timer {
    interval: 1500
    repeat: true
    running: controls.panelOpened
    triggeredOnStart: true
    onTriggered: controls.refresh()
  }

  Process {
    id: stateProc
    command: ["omarchy-nabu-display-control", "status"]
    stdout: StdioCollector { onStreamFinished: controls.accept(text) }
  }
  Process {
    id: actionProc
    stdout: StdioCollector { onStreamFinished: controls.accept(text) }
    onExited: function(code, status) {
      if (code !== 0) controls.failure = "Couldn't update display setting"
      controls.refresh()
    }
  }

  Toggle {
    width: parent.width
    label: "Auto-brightness"
    checked: controls.controlState.auto_brightness === true
    enabled: controls.controlState.brightness_available === true && !actionProc.running
    foreground: controls.foreground
    fontFamily: controls.fontFamily
    onClicked: { forceActiveFocus(); controls.request("toggle-brightness") }
  }
  Toggle {
    width: parent.width
    label: "Auto-rotate"
    checked: controls.controlState.auto_rotate === true
    enabled: controls.controlState.rotation_available === true && !actionProc.running
    foreground: controls.foreground
    fontFamily: controls.fontFamily
    onClicked: { forceActiveFocus(); controls.request("toggle-rotation") }
  }
  Text {
    width: parent.width
    visible: controls.failure !== ""
    text: controls.failure
    color: controls.foreground
    font.family: controls.fontFamily
    wrapMode: Text.WordWrap
  }
}
