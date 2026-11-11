using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;

namespace Wonju.PaintApp
{
    /// <summary>
    /// 꾸미기 모드 입력. 장식을 누르면 선택, 끌면 캐릭터 표면을 따라 옮김(달 수 없는 부위에서 놓으면 원래 자리로),
    /// 장식이 아닌 곳을 누르면 선택 해제, 배경을 끌면 회전. Delete 빼기, Esc 해제, [ ] 크기, Ctrl+Z·Ctrl+Y 꾸미기 되돌리기.
    /// </summary>
    public class DecorateController : MonoBehaviour
    {
        enum DragMode { None, Pending, MovingAccessory, Rotating, Transform }

        [SerializeField] AccessoryRig rig;
        [SerializeField] PaintCanvas canvas;
        [SerializeField] Camera viewCamera;
        [SerializeField] OrbitCamera orbit;
        [SerializeField] SelectionOutline outline;
        [SerializeField] float dragThreshold = 4f;
        [SerializeField] int undoSteps = 30;

        readonly InputAction press = new("Press", InputActionType.Button, "<Mouse>/leftButton");
        readonly InputAction point = new("Point", InputActionType.Value, "<Pointer>/position");
        readonly InputAction deleteKey = new("Delete", InputActionType.Button);
        readonly InputAction escapeKey = new("Escape", InputActionType.Button, "<Keyboard>/escape");
        readonly InputAction smallerKey = new("Smaller", InputActionType.Button, "<Keyboard>/leftBracket");
        readonly InputAction biggerKey = new("Bigger", InputActionType.Button, "<Keyboard>/rightBracket");
        readonly InputAction undoKey = new("Undo", InputActionType.Button, "<Keyboard>/z");
        readonly InputAction redoKey = new("Redo", InputActionType.Button, "<Keyboard>/y");
        readonly InputAction moveKey = new("MoveMode", InputActionType.Button, "<Keyboard>/w");
        readonly InputAction rotateKey = new("RotateMode", InputActionType.Button, "<Keyboard>/e");
        readonly InputAction scaleKey = new("ScaleMode", InputActionType.Button, "<Keyboard>/r");

        readonly List<AccessoryRig.State> undo = new();
        readonly List<AccessoryRig.State> redo = new();

        DragMode mode;
        AccessoryRig.Placed pressed;
        Vector2 pressScreen, lastScreen;
        AccessoryRig.State beforeDrag;
        bool lastMoveValid;
        AccessoryTransformGizmo gizmo;
        bool inputEnabled;

        public bool CanUndo => undo.Count > 0;
        public bool CanRedo => redo.Count > 0;
        public event Action HistoryChanged;
        public event Action TransformModeChanged;
        public AccessoryTransformMode TransformMode => gizmo != null ? gizmo.Mode : AccessoryTransformMode.Move;

        /// <summary>false면 입력을 받지 않습니다 (색칠 모드, 창이 열려 있을 때).</summary>
        public bool InputEnabled
        {
            get => inputEnabled;
            set
            {
                inputEnabled = value;
                if (!value) CancelDrag();
                if (gizmo != null) gizmo.Visible = value && isActiveAndEnabled;
            }
        }

        void Awake()
        {
            deleteKey.AddBinding("<Keyboard>/delete");
            deleteKey.AddBinding("<Keyboard>/backspace");
            gizmo = gameObject.AddComponent<AccessoryTransformGizmo>();
            gizmo.Initialize(rig, viewCamera);
        }

        void OnEnable()
        {
            foreach (var a in Actions()) a.Enable();
        }

        void OnDisable()
        {
            foreach (var a in Actions()) a.Disable();
            CancelDrag();
            if (gizmo != null) gizmo.Visible = false;
        }

        void Update()
        {
            if (!InputEnabled)
            {
                CancelDrag();
                return;
            }
            HandleKeys();

            var screen = point.ReadValue<Vector2>();
            gizmo.Visible = true;
            gizmo.SetPointer(screen);
            bool overUI = EventSystem.current != null && EventSystem.current.IsPointerOverGameObject();

            if (press.WasPressedThisFrame() && mode == DragMode.None && !overUI && !(orbit != null && orbit.IsDragging))
            {
                pressScreen = screen;
                if (gizmo.TryBegin(screen))
                {
                    beforeDrag = rig.Snapshot();
                    mode = DragMode.Transform;
                    if (orbit != null) orbit.InputBlocked = true;
                }
                else
                {
                    pressed = rig.Pick(viewCamera.ScreenPointToRay(screen), out _);
                    mode = DragMode.Pending;
                }
            }
            else if (press.IsPressed())
            {
                Continue(screen);
            }

            if (!press.IsPressed() && mode != DragMode.None)
                Release();
            lastScreen = screen;
        }

        void Continue(Vector2 screen)
        {
            switch (mode)
            {
                case DragMode.Transform:
                    gizmo.Drag(screen);
                    break;
                case DragMode.Pending:
                    if (Vector2.Distance(screen, pressScreen) < dragThreshold) break;
                    if (pressed != null)
                    {
                        rig.Select(pressed);
                        beforeDrag = rig.Snapshot();
                        mode = DragMode.MovingAccessory;
                        MoveSelected(screen);
                    }
                    else
                    {
                        mode = DragMode.Rotating;
                        orbit.Orbit(screen - pressScreen);
                    }
                    break;
                case DragMode.MovingAccessory:
                    MoveSelected(screen);
                    break;
                case DragMode.Rotating:
                    orbit.Orbit(screen - lastScreen);
                    break;
            }
        }

        void MoveSelected(Vector2 screen)
        {
            var ray = viewCamera.ScreenPointToRay(screen);
            lastMoveValid = canvas.Collider.Raycast(ray, out var hit, 100f) && rig.TryMoveTo(rig.Selected, hit);
            outline.Invalid = !lastMoveValid;
        }

        void Release()
        {
            switch (mode)
            {
                case DragMode.Transform:
                    if (gizmo.End(false)) Record(beforeDrag);
                    if (orbit != null) orbit.InputBlocked = false;
                    break;
                case DragMode.Pending:
                    // 끌지 않고 뗐으면 클릭: 장식이면 선택, 아니면 해제
                    rig.Select(pressed);
                    break;
                case DragMode.MovingAccessory:
                    if (lastMoveValid)
                    {
                        Record(beforeDrag);
                        rig.CommitMove(rig.Selected);
                    }
                    else
                    {
                        // 놓을 수 없는 자리에서 놓으면 끌기 전으로
                        var selectedIndex = IndexOf(rig.Selected);
                        rig.Restore(beforeDrag);
                        if (selectedIndex >= 0) rig.Select(rig.Items[selectedIndex]);
                    }
                    break;
            }
            outline.Invalid = false;
            mode = DragMode.None;
            pressed = null;
            beforeDrag = null;
        }

        void CancelDrag()
        {
            if (gizmo != null && gizmo.IsDragging) gizmo.End(true);
            if (mode == DragMode.Transform && orbit != null) orbit.InputBlocked = false;
            if (mode == DragMode.MovingAccessory && beforeDrag != null) rig.Restore(beforeDrag);
            if (outline != null) outline.Invalid = false;
            mode = DragMode.None;
            pressed = null;
            beforeDrag = null;
        }

        void HandleKeys()
        {
            if (mode == DragMode.Transform)
            {
                if (escapeKey.WasPressedThisFrame()) CancelDrag();
                return;
            }
            if (mode == DragMode.MovingAccessory) return;
            var keyboard = Keyboard.current;
            bool ctrl = keyboard != null && keyboard.ctrlKey.isPressed;
            if (ctrl)
            {
                bool shift = keyboard.shiftKey.isPressed;
                if (undoKey.WasPressedThisFrame()) { if (shift) Redo(); else Undo(); }
                if (redoKey.WasPressedThisFrame()) Redo();
                return;
            }
            if (deleteKey.WasPressedThisFrame()) RemoveSelected();
            if (escapeKey.WasPressedThisFrame()) rig.Select(null);
            if (smallerKey.WasPressedThisFrame()) ScaleSelected(-1);
            if (biggerKey.WasPressedThisFrame()) ScaleSelected(1);
            if (moveKey.WasPressedThisFrame()) SetTransformMode(AccessoryTransformMode.Move);
            if (rotateKey.WasPressedThisFrame()) SetTransformMode(AccessoryTransformMode.Rotate);
            if (scaleKey.WasPressedThisFrame()) SetTransformMode(AccessoryTransformMode.Scale);
        }

        public void SetTransformMode(AccessoryTransformMode value)
        {
            if (mode != DragMode.None || gizmo == null || gizmo.Mode == value) return;
            gizmo.Mode = value;
            TransformModeChanged?.Invoke();
        }

        // ───────────────────────── 패널·서랍에서 부르는 동작 (모두 되돌리기 기록) ─────────────────────────

        public void AddBuiltin(Wonju.CharacterShared.AccessoryDatabase.Definition definition)
        {
            if (!rig.CanAdd(false)) return;
            var before = rig.Snapshot();
            if (rig.AddBuiltin(definition) != null) Record(before);
        }

        public void AddScan(string itemId, string file, string name, GameObject template)
        {
            if (!rig.CanAdd(true)) return;
            var before = rig.Snapshot();
            if (rig.AddScan(itemId, file, name, template) != null) Record(before);
        }

        public void RotateSelected(float yawSteps, float tiltSteps) => Edit(() => rig.Rotate(rig.Selected, yawSteps, tiltSteps));
        public void ScaleSelected(int steps)
        {
            if (rig.CanScale(rig.Selected, steps)) Edit(() => rig.Scale(rig.Selected, steps));
        }
        public void ResetSelected() => Edit(() => rig.ResetPlacement(rig.Selected));
        public void RemoveSelected() => Edit(() => rig.Remove(rig.Selected));
        public void AttachSelected(string boneName)
        {
            if (!rig.CanAttachTo(rig.Selected, boneName) || rig.Selected.Entry.bone == boneName) return;
            Edit(() => rig.TrySetBone(rig.Selected, boneName));
        }

        void Edit(Action action)
        {
            if (rig.Selected == null || mode != DragMode.None) return;
            var before = rig.Snapshot();
            action();
            Record(before);
        }

        public void Undo() => Swap(undo, redo);
        public void Redo() => Swap(redo, undo);

        /// <summary>회차 초기화: 장식과 기록을 모두 지웁니다.</summary>
        public void ResetSession()
        {
            CancelDrag();
            rig.Clear();
            undo.Clear();
            redo.Clear();
            HistoryChanged?.Invoke();
        }

        void Record(AccessoryRig.State before)
        {
            undo.Add(before);
            if (undo.Count > undoSteps) undo.RemoveAt(0);
            redo.Clear();
            HistoryChanged?.Invoke();
        }

        void Swap(List<AccessoryRig.State> from, List<AccessoryRig.State> to)
        {
            if (from.Count == 0 || mode != DragMode.None) return;
            to.Add(rig.Snapshot());
            var state = from[^1];
            from.RemoveAt(from.Count - 1);
            rig.Restore(state);
            HistoryChanged?.Invoke();
        }

        int IndexOf(AccessoryRig.Placed placed)
        {
            for (int i = 0; i < rig.Items.Count; i++)
                if (rig.Items[i] == placed) return i;
            return -1;
        }

        InputAction[] Actions() => new[] { press, point, deleteKey, escapeKey, smallerKey, biggerKey, undoKey, redoKey, moveKey, rotateKey, scaleKey };
    }
}
