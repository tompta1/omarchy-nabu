import QtQuick
import Quickshell

// Run in a Wayland session: quickshell -n -p tests/qml/screen-move-remap
// Exercises the installed, patched helper without mapping any windows.
ShellRoot {
  Item {
    id: test
    property int pulses: 0
    property var guard: null
    QtObject { id: fakeScreen; property int x: 0; property int y: 0 }
    QtObject { id: fakeWindow; property var screen: fakeScreen }

    function require(condition, message) {
      if (!condition) {
        console.error('NABU_REMAP_TEST_FAIL', message)
        Qt.quit()
        throw new Error(message)
      }
    }
    Component.onCompleted: {
      var path = Quickshell.env('NABU_REMAP_TEST_COMPONENT') || '/usr/share/omarchy/shell/Ui/ScreenMoveRemap.qml'
      var component = Qt.createComponent(path)
      require(component.status === Component.Ready, component.errorString())
      guard = component.createObject(test, { window: fakeWindow })
      require(!!guard, 'helper did not instantiate')
      guard.remappingChanged.connect(function() { if (guard.remapping) test.pulses++ })
      falseNotification.start()
    }
    Timer {
      id: falseNotification
      interval: 300
      onTriggered: {
        test.require(test.pulses === 0, 'unexpected initial remap')
        // QScreen uses shared geometry notifications, including equal x/y.
        fakeScreen.xChanged()
        fakeScreen.yChanged()
        checkUnchanged.start()
      }
    }
    Timer {
      id: checkUnchanged
      interval: 350
      onTriggered: {
        test.require(test.pulses === 0, 'unchanged origin triggered remap')
        fakeScreen.x = 100
        checkMoved.start()
      }
    }
    Timer {
      id: checkMoved
      interval: 225
      onTriggered: {
        test.require(test.pulses === 1 && test.guard.remapping, 'real monitor move did not remap')
        checkSettled.start()
      }
    }
    Timer {
      id: checkSettled
      interval: 150
      onTriggered: {
        test.require(test.pulses === 1 && !test.guard.remapping, 'surface remained hidden')
        console.log('NABU_REMAP_TEST_PASS')
        Qt.quit()
      }
    }
  }
}
