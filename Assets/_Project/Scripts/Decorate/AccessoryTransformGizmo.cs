using UnityEngine;

namespace Wonju.PaintApp
{
    public enum AccessoryTransformMode { Move, Rotate, Scale }

    /// <summary>선택 장식의 모델 축 기즈모. 입력/Undo 트랜잭션은 DecorateController가 소유합니다.</summary>
    public class AccessoryTransformGizmo : MonoBehaviour
    {
        const int RingSegments = 64;
        const float HandlePixels = 90f;
        const float PickPixels = 10f;
        static Color AxisColor(int index) => index == 0 ? new Color(.94f, .22f, .22f)
            : index == 1 ? new Color(.22f, .82f, .36f) : new Color(.22f, .48f, 1f);

        AccessoryRig _rig;
        Camera _camera;
        GameObject _visualRoot;
        readonly LineRenderer[] _lines = new LineRenderer[4];
        readonly Material[] _materials = new Material[4];
        readonly Vector3[] _arrow = new Vector3[5];
        readonly Vector3[] _ring = new Vector3[RingSegments];
        readonly Vector3[] _square = new Vector3[4];
        AccessoryRig.Placed _dragged;
        int _axis = -1;
        Vector2 _pointer, _startScreen;
        Vector3 _pivot, _dragAxis, _startPosition, _startLocalPosition, _lastVector;
        Quaternion _startRotation, _startLocalRotation;
        float _startScale, _axisParameter, _angle;
        bool _changed;

        public AccessoryTransformMode Mode { get; set; }
        public bool Visible { get; set; }
        public bool IsDragging => _dragged != null;

        public void Initialize(AccessoryRig rig, Camera camera)
        {
            _rig = rig;
            _camera = camera;
            var shader = Resources.Load<Shader>("TransformGizmo");
            if (shader == null)
            {
                Debug.LogWarning("[TransformGizmo] 표시 셰이더를 찾지 못했습니다");
                return;
            }
            _visualRoot = new GameObject("Accessory Transform Handles");
            _visualRoot.transform.SetParent(transform, false);
            for (int i = 0; i < _lines.Length; i++)
            {
                var go = new GameObject(i < 3 ? "Axis " + "XYZ"[i] : "Uniform scale");
                go.transform.SetParent(_visualRoot.transform, false);
                var line = go.AddComponent<LineRenderer>();
                _materials[i] = new Material(shader) { name = "Transform Handle " + i, hideFlags = HideFlags.DontSave };
                line.sharedMaterial = _materials[i];
                line.useWorldSpace = true;
                line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                line.receiveShadows = false;
                line.numCapVertices = 3;
                line.enabled = false;
                _lines[i] = line;
            }
        }

        public void SetPointer(Vector2 screen) => _pointer = screen;

        Vector3 ModelAxis(int index) => _rig.ModelRoot.TransformDirection(index == 0 ? Vector3.right : index == 1 ? Vector3.up : Vector3.forward).normalized;

        Vector3 Center(AccessoryRig.Placed placed)
        {
            var filter = placed.Instance.GetComponent<MeshFilter>();
            return filter != null && filter.sharedMesh != null
                ? placed.Instance.TransformPoint(filter.sharedMesh.bounds.center) : placed.Instance.position;
        }

        float UnitsPerPixel(Vector3 pivot)
        {
            float depth = _camera.WorldToScreenPoint(pivot).z;
            if (depth <= _camera.nearClipPlane) return 0;
            return (_camera.orthographic ? 2f * _camera.orthographicSize
                : 2f * depth * Mathf.Tan(_camera.fieldOfView * Mathf.Deg2Rad * .5f)) / Mathf.Max(1, _camera.pixelHeight);
        }

        public bool TryBegin(Vector2 screen)
        {
            if (!Visible || _rig == null || _camera == null || _visualRoot == null || _rig.Selected == null) return false;
            int axis = Pick(screen);
            if (axis < 0) return false;
            var selected = _rig.Selected;
            var pivot = Center(selected);
            var ray = _camera.ScreenPointToRay(screen);
            var direction = axis < 3 ? ModelAxis(axis) : Vector3.zero;
            float parameter = 0;
            Vector3 vector = Vector3.zero;
            if (Mode == AccessoryTransformMode.Move && !TransformGizmoMath.TryAxisParameter(ray, pivot, direction, out parameter)) return false;
            if (Mode == AccessoryTransformMode.Rotate && !TransformGizmoMath.TryPlaneVector(ray, pivot, direction, out vector)) return false;
            _dragged = selected;
            _axis = axis;
            _pivot = pivot;
            _dragAxis = direction;
            _startScreen = screen;
            _startPosition = selected.Instance.position;
            _startRotation = selected.Instance.rotation;
            _startLocalPosition = selected.Instance.localPosition;
            _startLocalRotation = selected.Instance.localRotation;
            _startScale = selected.Instance.localScale.x;
            _axisParameter = parameter;
            _lastVector = vector;
            _angle = 0;
            _changed = false;
            return true;
        }

        public void Drag(Vector2 screen)
        {
            if (!IsDragging || _dragged.Instance == null || _rig.Selected != _dragged) return;
            var parent = _dragged.Instance.parent;
            Vector3 position = _startPosition;
            Quaternion rotation = _startRotation;
            float scale = _startScale;
            var ray = _camera.ScreenPointToRay(screen);
            if (Mode == AccessoryTransformMode.Move)
            {
                if (!TransformGizmoMath.TryAxisParameter(ray, _pivot, _dragAxis, out float parameter)) return;
                position += _dragAxis * (parameter - _axisParameter);
            }
            else if (Mode == AccessoryTransformMode.Rotate)
            {
                if (!TransformGizmoMath.TryPlaneVector(ray, _pivot, _dragAxis, out var vector)) return;
                _angle += TransformGizmoMath.SignedAngle(_lastVector, vector, _dragAxis);
                _lastVector = vector;
                var delta = Quaternion.AngleAxis(_angle, _dragAxis);
                rotation = delta * _startRotation;
                position = _pivot + delta * (_startPosition - _pivot);
            }
            else
            {
                var delta = screen - _startScreen;
                if (!TransformGizmoMath.TryUniformScale(_startScale, _dragged.Origin.scale, delta.x + delta.y, out scale)) return;
                position = _pivot + (_startPosition - _pivot) * (scale / _startScale);
            }
            var localPosition = parent.InverseTransformPoint(position);
            var localRotation = Quaternion.Inverse(parent.rotation) * rotation;
            if (_rig.TrySetTransform(_dragged, localPosition, localRotation, scale, false))
                _changed = Vector3.SqrMagnitude(localPosition - _startLocalPosition) > 1e-10f
                    || Quaternion.Angle(localRotation, _startLocalRotation) > .001f || Mathf.Abs(scale - _startScale) > 1e-6f;
        }

        public bool End(bool cancel)
        {
            if (!IsDragging) return false;
            bool changed = _changed;
            if (_dragged.Instance != null)
            {
                if (cancel) _rig.TrySetTransform(_dragged, _startLocalPosition, _startLocalRotation, _startScale, false);
                else if (changed) _rig.CommitMove(_dragged);
            }
            _dragged = null;
            _axis = -1;
            _changed = false;
            return changed && !cancel;
        }

        void LateUpdate()
        {
            bool show = Visible && _rig != null && _camera != null && _rig.Selected != null && _rig.Selected.Instance != null;
            if (_visualRoot == null) return;
            _visualRoot.SetActive(show);
            if (!show) return;
            var pivot = IsDragging ? _pivot : Center(_rig.Selected);
            // 이동 중에는 장식의 중심을 따라 핸들도 옮깁니다.
            if (IsDragging && Mode == AccessoryTransformMode.Move) pivot = Center(_dragged);
            float units = UnitsPerPixel(pivot);
            float length = units * HandlePixels;
            int hover = IsDragging ? _axis : Pick(_pointer);
            for (int i = 0; i < 3; i++)
            {
                var line = _lines[i];
                var axis = ModelAxis(i);
                bool visibleAxis = units > 0 && Mode != AccessoryTransformMode.Scale;
                if (Mode == AccessoryTransformMode.Move)
                    visibleAxis &= Vector2.Distance(_camera.WorldToScreenPoint(pivot), _camera.WorldToScreenPoint(pivot + axis * length)) > 18f;
                line.enabled = visibleAxis;
                if (!visibleAxis) continue;
                _materials[i].SetColor("_BaseColor", hover == i ? new Color(1, .83f, .18f) : AxisColor(i));
                line.widthMultiplier = units * (hover == i ? 3.5f : 2.5f);
                line.loop = Mode == AccessoryTransformMode.Rotate;
                if (Mode == AccessoryTransformMode.Move)
                {
                    var tip = pivot + axis * length;
                    var side = Vector3.Cross(axis, _camera.transform.forward).normalized;
                    _arrow[0] = pivot;
                    _arrow[1] = tip;
                    _arrow[2] = tip - axis * length * .17f + side * length * .08f;
                    _arrow[3] = tip;
                    _arrow[4] = tip - axis * length * .17f - side * length * .08f;
                    line.positionCount = _arrow.Length;
                    line.SetPositions(_arrow);
                }
                else
                {
                    FillRing(i, pivot, length, _ring);
                    line.positionCount = RingSegments;
                    line.SetPositions(_ring);
                }
            }
            _lines[3].enabled = units > 0 && Mode == AccessoryTransformMode.Scale;
            if (_lines[3].enabled)
            {
                var right = _camera.transform.right * units * 9;
                var up = _camera.transform.up * units * 9;
                _square[0] = pivot - right - up;
                _square[1] = pivot - right + up;
                _square[2] = pivot + right + up;
                _square[3] = pivot + right - up;
                _lines[3].loop = true;
                _lines[3].positionCount = 4;
                _lines[3].SetPositions(_square);
                _lines[3].widthMultiplier = units * 3;
                _materials[3].SetColor("_BaseColor", hover == 3 ? new Color(1, .83f, .18f) : Color.white);
            }
        }

        int Pick(Vector2 point)
        {
            if (_rig.Selected == null) return -1;
            var pivot = Center(_rig.Selected);
            float length = UnitsPerPixel(pivot) * HandlePixels;
            if (length <= 0) return -1;
            if (Mode == AccessoryTransformMode.Scale)
                return Vector2.Distance(point, _camera.WorldToScreenPoint(pivot)) <= 17f ? 3 : -1;
            int best = -1;
            float distance = PickPixels;
            for (int i = 0; i < 3; i++)
            {
                if (Mode == AccessoryTransformMode.Move)
                {
                    var a = _camera.WorldToScreenPoint(pivot + ModelAxis(i) * length * .18f);
                    var b = _camera.WorldToScreenPoint(pivot + ModelAxis(i) * length);
                    if (a.z <= 0 || b.z <= 0 || Vector2.Distance(a, b) < 15f) continue;
                    float d = SegmentDistance(point, a, b);
                    if (d < distance) { distance = d; best = i; }
                }
                else
                {
                    FillRing(i, pivot, length, _ring);
                    for (int j = 0; j < RingSegments; j++)
                    {
                        var a = _camera.WorldToScreenPoint(_ring[j]);
                        var b = _camera.WorldToScreenPoint(_ring[(j + 1) % RingSegments]);
                        if (a.z <= 0 || b.z <= 0) continue;
                        float d = SegmentDistance(point, a, b);
                        if (d < distance) { distance = d; best = i; }
                    }
                }
            }
            return best;
        }

        void FillRing(int axis, Vector3 center, float radius, Vector3[] points)
        {
            var a = ModelAxis((axis + 1) % 3);
            var b = ModelAxis((axis + 2) % 3);
            for (int j = 0; j < RingSegments; j++)
            {
                float angle = j * Mathf.PI * 2 / RingSegments;
                points[j] = center + (a * Mathf.Cos(angle) + b * Mathf.Sin(angle)) * radius;
            }
        }

        static float SegmentDistance(Vector2 point, Vector2 a, Vector2 b)
        {
            var delta = b - a;
            float t = delta.sqrMagnitude > 1e-6f ? Mathf.Clamp01(Vector2.Dot(point - a, delta) / delta.sqrMagnitude) : 0;
            return Vector2.Distance(point, a + delta * t);
        }

        void OnDestroy()
        {
            foreach (var material in _materials) if (material != null) Destroy(material);
        }
    }
}
