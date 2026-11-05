using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using Wonju.CharacterShared;

namespace Wonju.PaintApp
{
    /// <summary>서버의 공유 스캔을 차례로 받아 보관합니다. 삭제된 스캔도 이미 단 장식과 되돌리기를 위해 종료까지 보관합니다.</summary>
    public class ScanLibrary : MonoBehaviour
    {
        public sealed class Item
        {
            public string ItemId, File, Name;
            public Texture2D Thumb;
            public GameObject Template;
        }

        [Serializable]
        sealed class Response
        {
            public Scan[] scans;
            public int max = 40;
        }

        [Serializable]
        sealed class Scan
        {
            public string itemId, file, thumb, name;
        }

        sealed class Cached
        {
            public Item Item;
            public GlbLoader.Result Loaded;
            public string ThumbPath;
            public int Failures;
        }

        [SerializeField] private Shader _scanShader;
        [SerializeField] private float _pollSeconds = 5f;

        readonly List<Item> _items = new();
        readonly Dictionary<string, Cached> _cache = new();
        AppConfig _config;
        Coroutine _poll;
        UnityWebRequest _request;
        UnityWebRequest _assetRequest;
        Response _latest;
        bool _loading;

        public IReadOnlyList<Item> Items => _items;
        public int Max { get; private set; } = 40;
        public event Action Changed;

        public void Begin(AppConfig config)
        {
            if (_poll != null || config == null) return;
            _config = config;
            _poll = StartCoroutine(Poll());
        }

        public (GameObject template, string name)? Resolve(string itemId)
        {
            if (itemId != null && _cache.TryGetValue(itemId, out var cached) && cached.Item.Template != null)
                return (cached.Item.Template, cached.Item.Name);
            return null;
        }

        IEnumerator Poll()
        {
            while (true)
            {
                Response response = null;
                using (var request = UnityWebRequest.Get(Url("api/scans")))
                {
                    _request = request;
                    request.timeout = 10;
                    yield return request.SendWebRequest();
                    _request = null;
                    if (request.result == UnityWebRequest.Result.Success)
                    {
                        try { response = JsonUtility.FromJson<Response>(request.downloadHandler.text); }
                        catch (ArgumentException) { }
                    }
                }

                // 연결 실패나 잘못된 응답이면 마지막으로 받은 목록을 유지합니다.
                if (response?.scans != null)
                {
                    _latest = response;
                    Refresh(response);
                    if (!_loading)
                    {
                        _loading = true;
                        StartCoroutine(LoadMissing(response));
                    }
                }
                yield return new WaitForSecondsRealtime(Mathf.Max(1f, _pollSeconds));
            }
        }

        IEnumerator LoadMissing(Response response)
        {
            var seen = new HashSet<string>();
            foreach (var scan in response.scans)
            {
                if (scan == null || string.IsNullOrEmpty(scan.itemId) || string.IsNullOrEmpty(scan.file) || !seen.Add(scan.itemId)) continue;
                if (!Array.Exists(_latest.scans, current => current != null && current.itemId == scan.itemId)) continue;
                var cached = _cache[scan.itemId];
                if (cached.Failures >= 3) continue;
                if (cached.Loaded == null)
                {
                    yield return Load(scan, cached);
                    Refresh(_latest);
                }
                if (cached.Loaded != null && !string.IsNullOrEmpty(scan.thumb) &&
                    (cached.Item.Thumb == null || cached.ThumbPath != scan.thumb))
                {
                    yield return LoadThumb(scan, cached);
                }
            }
            _loading = false;
        }

        void Refresh(Response response)
        {
            bool changed = Max != response.max;
            Max = response.max;
            var next = new List<Item>();
            var seen = new HashSet<string>();
            foreach (var scan in response.scans)
            {
                if (scan == null || string.IsNullOrEmpty(scan.itemId) || string.IsNullOrEmpty(scan.file) || !seen.Add(scan.itemId)) continue;
                if (!_cache.TryGetValue(scan.itemId, out var cached))
                {
                    cached = new Cached { Item = new Item { ItemId = scan.itemId } };
                    _cache.Add(scan.itemId, cached);
                }
                var item = cached.Item;
                string name = string.IsNullOrEmpty(scan.name) ? scan.itemId : scan.name;
                changed |= item.Name != name || item.File != scan.file;
                item.Name = name;
                item.File = scan.file;
                if (string.IsNullOrEmpty(scan.thumb) && item.Thumb != null)
                {
                    Destroy(item.Thumb);
                    item.Thumb = null;
                    cached.ThumbPath = null;
                    changed = true;
                }
                if (item.Template != null) next.Add(item);
            }
            changed |= next.Count != _items.Count;
            if (!changed)
                for (int i = 0; i < next.Count; i++)
                    if (next[i] != _items[i]) { changed = true; break; }
            _items.Clear();
            _items.AddRange(next);
            if (changed) Changed?.Invoke();
        }

        IEnumerator Load(Scan scan, Cached cached)
        {
            using var request = UnityWebRequest.Get(Url(scan.file));
            _assetRequest = request;
            request.timeout = 10;
            yield return request.SendWebRequest();
            _assetRequest = null;
            if (request.result != UnityWebRequest.Result.Success)
            {
                Failed(cached, request.error);
                yield break;
            }
            try
            {
                if (_scanShader == null) throw new InvalidOperationException("스캔 셰이더가 연결되지 않았습니다");
                cached.Loaded = GlbLoader.Load(request.downloadHandler.data, _scanShader, scan.itemId);
                cached.Item.Template = cached.Loaded.Template;
                cached.Item.Template.transform.SetParent(transform, false);
            }
            catch (Exception e) { Failed(cached, e.Message); }
        }

        IEnumerator LoadThumb(Scan scan, Cached cached)
        {
            using var request = UnityWebRequestTexture.GetTexture(Url(scan.thumb));
            _assetRequest = request;
            request.timeout = 10;
            yield return request.SendWebRequest();
            _assetRequest = null;
            if (request.result != UnityWebRequest.Result.Success)
            {
                Failed(cached, request.error);
                yield break;
            }
            Texture2D thumb = null;
            string error = null;
            try { thumb = DownloadHandlerTexture.GetContent(request); }
            catch (Exception e) { error = e.Message; }
            if (thumb == null)
            {
                Failed(cached, error ?? "썸네일을 읽을 수 없습니다");
                yield break;
            }
            if (cached.Item.Thumb != null) Destroy(cached.Item.Thumb);
            cached.Item.Thumb = thumb;
            cached.ThumbPath = scan.thumb;
            Changed?.Invoke();
        }

        void Failed(Cached cached, string reason)
        {
            if (++cached.Failures == 3)
                Debug.LogWarning($"[ScanLibrary] {cached.Item.ItemId} 받기 3회 실패, 이번 실행에서는 건너뜁니다: {reason}");
        }

        string Url(string path) => $"{_config.serverUrl.TrimEnd('/')}/{path.TrimStart('/')}";

        void OnDestroy()
        {
            _request?.Abort();
            _assetRequest?.Abort();
            StopAllCoroutines();
            if (_request != null)
            {
                _request.Dispose();
                _request = null;
            }
            if (_assetRequest != null)
            {
                _assetRequest.Dispose();
                _assetRequest = null;
            }
            foreach (var cached in _cache.Values)
            {
                cached.Loaded?.Dispose();
                if (cached.Item.Thumb != null) Destroy(cached.Item.Thumb);
            }
            _cache.Clear();
            _items.Clear();
            Changed = null;
        }
    }
}
