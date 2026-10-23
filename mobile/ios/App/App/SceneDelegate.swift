import UIKit
import Capacitor

// iOS 27 ends an app built with the iOS 27 SDK at launch unless it adopts the scene lifecycle
// (UIKit traps in _UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption). This is that
// adoption, wired up by UIApplicationSceneManifest in Info.plist.
//
// The window is not built here. The manifest names Main.storyboard, so UIKit creates the window,
// sets it on this delegate and loads the storyboard's CAPBridgeViewController, exactly as
// UIMainStoryboardFile did before. Building one in code as well would start a second bridge.
class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }

    // Under scenes UIKit delivers opened URLs and universal links here, not to the app delegate.
    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        SceneDelegateProxy.shared.scene(scene, openURLContexts: URLContexts)
    }

    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }
}
