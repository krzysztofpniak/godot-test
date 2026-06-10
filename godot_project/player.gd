extends Node3D

const SPEED := 4.0
const MOUSE_SENSITIVITY := 0.0025

@onready var camera: Camera3D = $XROrigin3D/XRCamera3D

var vr_active := false
var pitch := 0.0


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	elif event.is_action_pressed("ui_cancel"):
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE

	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED and not vr_active:
		rotate_y(-event.relative.x * MOUSE_SENSITIVITY)
		pitch = clamp(pitch - event.relative.y * MOUSE_SENSITIVITY, -1.5, 1.5)
		camera.rotation.x = pitch


func _process(delta: float) -> void:
	var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var vertical := Input.get_axis("move_down", "move_up")

	var basis := camera.global_transform.basis
	var move := basis.x * input_dir.x + basis.z * input_dir.y
	move.y = 0
	if move.length() > 0.0:
		move = move.normalized()
	move += Vector3.UP * vertical

	global_translate(move * SPEED * delta)
