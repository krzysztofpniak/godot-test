extends CharacterBody3D

const SPEED := 4.0
const JUMP_VELOCITY := 5.0
const GRAVITY := 9.8
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


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y -= GRAVITY * delta

	if Input.is_action_just_pressed("jump") and is_on_floor():
		velocity.y = JUMP_VELOCITY

	var input_dir := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var basis := camera.global_transform.basis
	var move := basis.x * input_dir.x + basis.z * input_dir.y
	move.y = 0
	if move.length() > 0.0:
		move = move.normalized()

	velocity.x = move.x * SPEED
	velocity.z = move.z * SPEED

	move_and_slide()
