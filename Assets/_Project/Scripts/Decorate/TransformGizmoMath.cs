using UnityEngine;

namespace Wonju.PaintApp
{
    public static class TransformGizmoMath
    {
        public static bool IsFinite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        public static bool IsFinite(Vector3 value) => IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        public static bool IsFinite(Quaternion value) => IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z) && IsFinite(value.w);

        public static bool TryAxisParameter(Ray ray, Vector3 origin, Vector3 axis, out float parameter)
        {
            parameter = 0;
            if (!IsFinite(ray.origin) || !IsFinite(ray.direction) || !IsFinite(origin) || !IsFinite(axis)
                || axis.sqrMagnitude < 1e-8f || ray.direction.sqrMagnitude < 1e-8f) return false;
            axis.Normalize();
            var direction = ray.direction.normalized;
            float alignment = Vector3.Dot(axis, direction);
            float denominator = 1f - alignment * alignment;
            if (denominator < 1e-4f) return false;
            var offset = ray.origin - origin;
            parameter = (Vector3.Dot(axis, offset) - alignment * Vector3.Dot(direction, offset)) / denominator;
            return IsFinite(parameter);
        }

        public static bool TryPlaneVector(Ray ray, Vector3 origin, Vector3 normal, out Vector3 vector)
        {
            vector = Vector3.zero;
            if (!IsFinite(ray.origin) || !IsFinite(ray.direction) || !IsFinite(origin) || !IsFinite(normal)
                || normal.sqrMagnitude < 1e-8f) return false;
            var plane = new Plane(normal.normalized, origin);
            if (!plane.Raycast(ray, out float distance) || !IsFinite(distance)) return false;
            var offset = ray.GetPoint(distance) - origin;
            if (!IsFinite(offset) || offset.sqrMagnitude < 1e-8f) return false;
            vector = offset.normalized;
            return true;
        }

        public static float SignedAngle(Vector3 from, Vector3 to, Vector3 axis) => Vector3.SignedAngle(from, to, axis);

        public static bool TryUniformScale(float start, float origin, float pixels, out float scale)
        {
            scale = start;
            if (!IsFinite(start) || !IsFinite(origin) || !IsFinite(pixels) || start <= 0 || origin <= 0
                || !IsFinite(origin * AccessoryRig.MaxScaleFactor)) return false;
            float factor = Mathf.Exp(Mathf.Clamp(pixels * .01f, -10f, 10f));
            scale = Mathf.Clamp(start * factor, origin * AccessoryRig.MinScaleFactor, origin * AccessoryRig.MaxScaleFactor);
            return IsFinite(scale);
        }
    }
}
