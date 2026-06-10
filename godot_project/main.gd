extends Node3D

var webxr_interface: XRInterface

func _ready() -> void:
	$CanvasLayer/EnterVRButton.pressed.connect(_on_enter_vr_pressed)
	_generate_level_collision($level)

	webxr_interface = XRServer.find_interface("WebXR")
	if webxr_interface:
		webxr_interface.session_supported.connect(_on_session_supported)
		webxr_interface.session_started.connect(_on_session_started)
		webxr_interface.session_ended.connect(_on_session_ended)
		webxr_interface.session_failed.connect(_on_session_failed)
		webxr_interface.is_session_supported("immersive-vr")
	else:
		$CanvasLayer/StatusLabel.text = "WebXR not available (open this in a browser export)"


func _generate_level_collision(node: Node) -> void:
	if node is MeshInstance3D:
		node.create_trimesh_collision()
	for child in node.get_children():
		_generate_level_collision(child)


func _on_session_supported(session_mode: String, supported: bool) -> void:
	if session_mode == "immersive-vr":
		if supported:
			$CanvasLayer/StatusLabel.text = "VR ready - click Enter VR"
		else:
			$CanvasLayer/StatusLabel.text = "Immersive VR not supported on this device/browser"


func _on_enter_vr_pressed() -> void:
	if not webxr_interface:
		return

	webxr_interface.session_mode = "immersive-vr"
	webxr_interface.requested_reference_space_types = "local-floor, local"
	webxr_interface.required_features = "local-floor"
	webxr_interface.optional_features = "bounded-floor"

	if not webxr_interface.initialize():
		$CanvasLayer/StatusLabel.text = "Failed to initialize WebXR session"


func _on_session_started() -> void:
	get_viewport().use_xr = true
	$CanvasLayer.visible = false
	$Player.vr_active = true


func _on_session_ended() -> void:
	get_viewport().use_xr = false
	$CanvasLayer.visible = true
	$Player.vr_active = false


func _on_session_failed(message: String) -> void:
	if message.contains("multiview"):
		$CanvasLayer/StatusLabel.text = "This browser/GPU doesn't support stereo XR rendering (no multiview). Try a Quest headset's browser."
	else:
		$CanvasLayer/StatusLabel.text = "WebXR session failed: " + message
