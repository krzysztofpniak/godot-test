extends CharacterBody3D

const SPEED := 4.0
const JUMP_VELOCITY := 5.0
const GRAVITY := 9.8
const MOUSE_SENSITIVITY := 0.0025
const TURN_SPEED := 2.0
const STICK_DEADZONE := 0.2

@onready var camera: Camera3D = $XROrigin3D/XRCamera3D
@onready var left_controller: XRController3D = $XROrigin3D/LeftController
@onready var right_controller: XRController3D = $XROrigin3D/RightController

var vr_active := false
var pitch := 0.0
var controller_jump_held := false


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

	var move_input := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
	var jump_pressed := Input.is_action_just_pressed("jump")

	if vr_active:
		var left_stick: Vector2 = left_controller.get_vector2("thumbstick")
		if left_stick.length() > STICK_DEADZONE:
			move_input = Vector2(left_stick.x, -left_stick.y)
		else:
			move_input = Vector2.ZERO

		var right_stick: Vector2 = right_controller.get_vector2("thumbstick")
		if abs(right_stick.x) > STICK_DEADZONE:
			rotate_y(-right_stick.x * TURN_SPEED * delta)

		var jump_held := left_controller.is_button_pressed("ax_button") or right_controller.is_button_pressed("ax_button")
		if jump_held and not controller_jump_held:
			jump_pressed = true
		controller_jump_held = jump_held

	if jump_pressed and is_on_floor():
		velocity.y = JUMP_VELOCITY

	# Debug spheres: move with thumbstick input, glow yellow when the
	# jump button on that controller is pressed.
	var left_stick_dbg: Vector2 = left_controller.get_vector2("thumbstick")
	var right_stick_dbg: Vector2 = right_controller.get_vector2("thumbstick")
	left_debug_sphere.position = left_debug_base + Vector3(left_stick_dbg.x, -left_stick_dbg.y, 0) * 0.1
	right_debug_sphere.position = right_debug_base + Vector3(right_stick_dbg.x, -right_stick_dbg.y, 0) * 0.1

	var left_glow := left_controller.is_button_pressed("ax_button")
	var right_glow := right_controller.is_button_pressed("ax_button")
	left_debug_sphere.scale = Vector3.ONE * (2.0 if left_glow else 1.0)
	right_debug_sphere.scale = Vector3.ONE * (2.0 if right_glow else 1.0)

	var basis := camera.global_transform.basis
	var move := basis.x * move_input.x + basis.z * move_input.y
	move.y = 0
	if move.length() > 0.0:
		move = move.normalized()

	velocity.x = move.x * SPEED
	velocity.z = move.z * SPEED

	move_and_slide()
