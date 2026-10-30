using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;
using Wonju.CharacterShared;

namespace Wonju.PaintApp
{
    /// <summary>
    /// 캐릭터(텍스처 PNG + 규약 JSON)를 운영 PC 서버(POST /api/character)로 올립니다. 규약: Contract/CONTRACT.md
    /// 보내기 전에 로컬 백업을 남기고, 실패하면 간격을 늘려 가며 다시 시도합니다.
    /// </summary>
    public class TextureUploader
    {
        /// <summary>업로드 결과. Rejected = 서버가 내용을 거절(4xx)해서 다시 보내도 같은 경우.</summary>
        public readonly struct Result
        {
            public readonly bool Ok;
            public readonly bool Rejected;
            public readonly string Error;

            public Result(bool ok, bool rejected, string error)
            {
                Ok = ok;
                Rejected = rejected;
                Error = error;
            }
        }

        readonly AppConfig config;
        readonly int attempts;
        readonly int timeoutSeconds;

        public TextureUploader(AppConfig config, int attempts = 3, int timeoutSeconds = 10)
        {
            this.config = config;
            this.attempts = attempts;
            this.timeoutSeconds = timeoutSeconds;
        }

        public string LastBackupPath { get; private set; }

        /// <summary>업로드 코루틴. data.id는 업로드 id와 같아야 합니다 (서버가 확인).</summary>
        public IEnumerator Upload(byte[] png, CharacterData data, Action<Result> onDone)
        {
            string json = JsonUtility.ToJson(data);
            SaveBackup(png, json);

            string error = null;
            for (int attempt = 1; attempt <= attempts; attempt++)
            {
                using var request = UnityWebRequest.Post($"{config.serverUrl}/api/character", new List<IMultipartFormSection>
                {
                    new MultipartFormDataSection("id", data.id.ToString()),
                    new MultipartFormDataSection("json", json),
                    new MultipartFormFileSection("file", png, CharacterData.TextureFileName(data.id), "image/png"),
                });
                request.timeout = timeoutSeconds;
                yield return request.SendWebRequest();

                if (request.result == UnityWebRequest.Result.Success)
                {
                    // 응답의 warnings = 서버가 규약에 맞지 않아 버린 장식
                    Debug.Log($"[Upload] 캐릭터 {data.id} 업로드 성공 ({png.Length / 1024} KB): {request.downloadHandler.text}");
                    onDone(new Result(true, false, null));
                    yield break;
                }

                error = request.responseCode > 0 ? $"HTTP {request.responseCode} {request.downloadHandler?.text}" : request.error;
                Debug.LogWarning($"[Upload] {attempt}/{attempts}번째 시도 실패: {error}");
                // 서버가 요청 자체를 거절(4xx)했으면 다시 보내도 같으므로 멈춥니다.
                if (request.responseCode is >= 400 and < 500)
                {
                    onDone(new Result(false, true, error));
                    yield break;
                }
                if (attempt < attempts) yield return new WaitForSecondsRealtime(attempt);
            }
            onDone(new Result(false, false, error));
        }

        void SaveBackup(byte[] png, string json)
        {
            try
            {
                Directory.CreateDirectory(config.BackupPath);
                string id = config.HasValidStudentId ? config.Id.ToString() : "unknown";
                LastBackupPath = Path.Combine(config.BackupPath, $"student{id}_{DateTime.Now:yyyyMMdd_HHmmss}.png");
                File.WriteAllBytes(LastBackupPath, png);
                File.WriteAllText(Path.ChangeExtension(LastBackupPath, ".json"), json);
            }
            catch (Exception e)
            {
                Debug.LogError($"[Upload] 백업 저장 실패: {e.Message}");
            }
        }
    }

    /// <summary>서버 /api/health를 주기적으로 확인합니다.</summary>
    public class ServerStatus
    {
        readonly AppConfig config;

        public ServerStatus(AppConfig config) => this.config = config;

        public bool? Connected { get; private set; }
        public event Action<bool> Changed;

        public IEnumerator Poll()
        {
            while (true)
            {
                using (var request = UnityWebRequest.Get($"{config.serverUrl}/api/health"))
                {
                    request.timeout = 3;
                    yield return request.SendWebRequest();
                    bool ok = request.result == UnityWebRequest.Result.Success;
                    if (Connected != ok)
                    {
                        Connected = ok;
                        Changed?.Invoke(ok);
                    }
                }
                yield return new WaitForSecondsRealtime(Mathf.Max(1f, config.healthCheckSeconds));
            }
        }
    }
}
