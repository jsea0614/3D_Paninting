using System;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;
using Wonju.CharacterShared;

namespace Wonju.Tests
{
    public class TransformGizmoTests
    {
        // Reflection lets the first RED run compile before the new runtime API exists.
        static object Invoke(string method, params object[] args)
        {
            var type = Type.GetType("Wonju.PaintApp.TransformGizmoMath, Wonju.PaintApp");
            Assert.That(type, Is.Not.Null, "Transform gizmo calculations are not implemented yet");
            return type.GetMethod(method, BindingFlags.Public | BindingFlags.Static).Invoke(null, args);
        }

        [Test]
        public void AxisDragFindsWorldOffset()
        {
            object[] args = { new Ray(new Vector3(2, 1, -5), new Vector3(0, -1, 5)), Vector3.zero, Vector3.right, 0f };
            Assert.That((bool)Invoke("TryAxisParameter", args), Is.True);
            Assert.That((float)args[3], Is.EqualTo(2f).Within(1e-4f));
        }

        [Test]
        public void AxisDragRespectsTranslatedAndRotatedModelAxis()
        {
            object[] args = { new Ray(new Vector3(3, 4, -5), Vector3.forward), new Vector3(3, 1, 0), Vector3.up, 0f };
            Assert.That((bool)Invoke("TryAxisParameter", args), Is.True);
            Assert.That((float)args[3], Is.EqualTo(3f).Within(1e-4f));
        }

        [Test]
        public void AxisParallelToViewDoesNotProduceHugeJump()
        {
            object[] args = { new Ray(new Vector3(0, 0, -5), Vector3.forward), Vector3.zero, Vector3.forward, 0f };
            Assert.That((bool)Invoke("TryAxisParameter", args), Is.False);
        }

        [Test]
        public void RotationPlaneGivesNormalizedDirection()
        {
            object[] args = { new Ray(new Vector3(2, 0, -5), Vector3.forward), Vector3.zero, Vector3.forward, Vector3.zero };
            Assert.That((bool)Invoke("TryPlaneVector", args), Is.True);
            Assert.That(Vector3.Distance((Vector3)args[3], Vector3.right), Is.LessThan(1e-5));
        }

        [Test]
        public void RotationPlaneRejectsParallelRayAndCenterClick()
        {
            object[] parallel = { new Ray(new Vector3(0, 0, -5), Vector3.right), Vector3.zero, Vector3.forward, Vector3.zero };
            Assert.That((bool)Invoke("TryPlaneVector", parallel), Is.False);
            object[] center = { new Ray(new Vector3(0, 0, -5), Vector3.forward), Vector3.zero, Vector3.forward, Vector3.zero };
            Assert.That((bool)Invoke("TryPlaneVector", center), Is.False);
        }

        [Test]
        public void RotationKeepsClockwiseAndCounterclockwiseSigns()
        {
            Assert.That((float)Invoke("SignedAngle", Vector3.right, Vector3.up, Vector3.forward), Is.EqualTo(90).Within(.001));
            Assert.That((float)Invoke("SignedAngle", Vector3.up, Vector3.right, Vector3.forward), Is.EqualTo(-90).Within(.001));
        }

        [TestCase(1000f, .6f)]
        [TestCase(-1000f, .15f)]
        [TestCase(0f, .3f)]
        public void UniformScaleUsesExistingRelativeLimits(float pixels, float expected)
        {
            object[] args = { .3f, .3f, pixels, 0f };
            Assert.That((bool)Invoke("TryUniformScale", args), Is.True);
            Assert.That((float)args[3], Is.EqualTo(expected).Within(1e-5));
        }

        [TestCase(float.NaN)]
        [TestCase(float.PositiveInfinity)]
        public void InvalidInputDoesNotEnterTransform(float invalid)
        {
            object[] axis = { new Ray(new Vector3(invalid, 0, -5), Vector3.forward), Vector3.zero, Vector3.right, 0f };
            Assert.That((bool)Invoke("TryAxisParameter", axis), Is.False);
            object[] scale = { .3f, .3f, invalid, 0f };
            Assert.That((bool)Invoke("TryUniformScale", scale), Is.False);
        }

        [Test]
        public void RigTransformPreservesBoneAndExportsRoundtripPose()
        {
            var rigType = Type.GetType("Wonju.PaintApp.AccessoryRig, Wonju.PaintApp");
            var setter = rigType.GetMethod("TrySetTransform");
            Assert.That(setter, Is.Not.Null, "Rig transform commit is not implemented yet");
            var model = new GameObject("model");
            var host = new GameObject("rig");
            var template = new GameObject("template", typeof(MeshFilter), typeof(MeshRenderer));
            var mesh = new Mesh { vertices = new[] { new Vector3(-.25f, -.25f, -.25f), new Vector3(.25f, .25f, .25f), new Vector3(.25f, -.25f, .25f) }, triangles = new[] { 0, 1, 2 } };
            mesh.RecalculateBounds();
            template.GetComponent<MeshFilter>().sharedMesh = mesh;
            template.SetActive(false);
            try
            {
                var hand = new GameObject("Right_Hand_Socket").transform;
                hand.SetParent(model.transform);
                hand.localPosition = new Vector3(.2f, .6f, 0);
                model.transform.SetPositionAndRotation(new Vector3(2, 3, 4), Quaternion.Euler(0, 45, 0));
                var rig = host.AddComponent(rigType);
                rigType.GetField("characterRoot", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(rig, model.transform);
                var placed = rigType.GetMethod("AddScan").Invoke(rig, new object[] { "scan_test", "scans/test.glb", "test", template });
                var instance = (Transform)placed.GetType().GetField("Instance").GetValue(placed);
                var rotation = Quaternion.Euler(20, 35, 10);
                Assert.That((bool)setter.Invoke(rig, new object[] { placed, new Vector3(.1f, .45f, -.2f), rotation, .35f, true }), Is.True);
                Assert.That(instance.parent, Is.SameAs(hand));
                var entries = (List<AccessoryEntry>)rigType.GetMethod("ToEntries").Invoke(rig, null);
                Assert.That(entries[0].bone, Is.EqualTo("Right_Hand_Socket"));
                Assert.That(entries[0].pos, Is.EqualTo(new[] { .1f, .45f, -.2f }));
                Assert.That(entries[0].scale, Is.EqualTo(.35f));
                var copy = new GameObject("roundtrip").transform;
                CharacterContract.Apply(entries[0], hand, copy);
                Assert.That(Vector3.Distance(copy.position, instance.position), Is.LessThan(1e-4));
                Assert.That(Quaternion.Angle(copy.rotation, instance.rotation), Is.LessThan(.02));
                var before = instance.localPosition;
                Assert.That((bool)setter.Invoke(rig, new object[] { placed, new Vector3(float.NaN, 0, 0), rotation, .35f, true }), Is.False);
                Assert.That(instance.localPosition, Is.EqualTo(before));

                // A scan moved above the head must be able to follow Head instead of its original hand.
                var setBone = rigType.GetMethod("TrySetBone");
                Assert.That(setBone, Is.Not.Null, "Explicit attachment selection is not implemented yet");
                var head = new GameObject("Head").transform;
                head.SetParent(model.transform);
                head.localPosition = new Vector3(0, 1.2f, 0);
                head.localRotation = Quaternion.Euler(10, 25, 0);
                var worldPosition = instance.position;
                var worldRotation = instance.rotation;
                Assert.That((bool)setBone.Invoke(rig, new object[] { placed, "Head" }), Is.True);
                Assert.That(instance.parent, Is.SameAs(head));
                Assert.That(Vector3.Distance(instance.position, worldPosition), Is.LessThan(1e-5));
                Assert.That(Quaternion.Angle(instance.rotation, worldRotation), Is.LessThan(.02));
                entries = (List<AccessoryEntry>)rigType.GetMethod("ToEntries").Invoke(rig, null);
                Assert.That(entries[0].bone, Is.EqualTo("Head"));
                CharacterContract.Apply(entries[0], head, copy);
                Assert.That(Vector3.Distance(copy.position, instance.position), Is.LessThan(1e-4));
                Assert.That(Quaternion.Angle(copy.rotation, instance.rotation), Is.LessThan(.02));
                head.position += Vector3.up;
                hand.position += Vector3.right;
                Assert.That(Vector3.Distance(instance.position, worldPosition + Vector3.up), Is.LessThan(1e-5));
                Assert.That((bool)setBone.Invoke(rig, new object[] { placed, "MissingBone" }), Is.False);
                placed.GetType().GetField("AllowedBones").SetValue(placed, new[] { "Head" });
                Assert.That((bool)setBone.Invoke(rig, new object[] { placed, "Right_Hand_Socket" }), Is.False);
                Assert.That(instance.parent, Is.SameAs(head));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(host);
                UnityEngine.Object.DestroyImmediate(model);
                UnityEngine.Object.DestroyImmediate(template);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }
    }
}
