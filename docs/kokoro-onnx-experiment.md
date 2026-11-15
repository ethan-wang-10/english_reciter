# Kokoro ONNX 默写朗读实验

状态：默认关闭的实验。模型在独立 CPU 进程运行，网站进程不导入或加载模型。保持现有清单、读音修正、错题、正常语速、每条两遍及手动下一条。

## 结构

- 网站通过 `DICTATION_KOKORO_URL=http://127.0.0.1:8877` 显式连接独立 worker；未设置时沿用现有朗读。
- 英语：Kokoro → Piper → 设备英语语音。语文：Kokoro → 设备普通话。登录失效时要求重新登录。
- 保存清单、开始默写和保存读音后，页面异步提交实际 `spoken_text` 提前生成；不阻塞保存或开始，不自动播放。单批英语最多400条、语文200条，错题大清单先准备首批，后续仍可按需朗读。
- 单 worker 只加载一个 ONNX Runtime CPU session，合成线程数1、并发1；有界后台队列，排队重复项合并。忙碌时音频请求尽快失败，网页沿现有路径朗读。
- 缓存键包含模型、音色与可选词表文件 SHA256、前端/运行库版本、语言、音色、速度和实际朗读文字。修改读音不会命中旧读法；磁盘命中无需加载模型。

## 本轮实测结果

实测为本机 Apple M3 Max／128 GiB，模型只使用 ONNX Runtime CPU 单线程，没有使用 GPU 或 Metal，不代表现有双核云主机速度。

| 项目 | 结果 |
| --- | --- |
| 中英文测试 | 25/25 生成有效完整 WAV，缓存再次播放逐字节一致 |
| 首条含加载 | 1.86 秒 |
| 短项首次 | 0.51–2.60 秒，中位1.20秒 |
| Worker缓存命中 | 中位0.81毫秒 |
| 网页网关缓存命中 | 本机中英、学生／家长及500字符长句约2–4毫秒 |
| 500字符长句 | 首次17.21秒，音频29.86秒；530音素分510＋20，未截断 |
| Worker峰值RSS | 约1.34 GiB |
| 预取与异常缓存 | 去重、重复命中和损坏文件重新生成通过 |

这25项通过的是输入覆盖、完整WAV与数值有效性检查，不能替代教师对读音、自然度和口音的试听。原INT8和社区FP16导出的失败结果均保留在本轮实验记录中，没有用无效音频冒充通过。

现有生产主机2核2GB，实测时可用内存约502MB，低于本机worker单独占用；本轮未在生产安装或加载模型，未打开线上实验开关。后续应先考虑增加CPU主机内存或离线生成再缓存，并在目标主机重新验收，不能凭本机结果保证云主机延迟。

## 隔离安装

推荐 Python 3.12 独立虚拟环境，只安装 `requirements-kokoro-experiment.txt`。不要安装 `misaki[en]`，它会带入不需要的 PyTorch/Transformers；英文使用 phonemizer 与 espeakng-loader。

本轮先测了发布版 `kokoro-v1.1-zh.int8.onnx`（约108.83 MiB），其在 Mac ARM64／ONNX Runtime 1.32 上输出 NaN；1.22 缺少所需 ConvInteger 算子，不能把这些输出当作正常音频。另一个 onnx-community FP16 导出在较长英文上仍会返回无效音频。

最终验证候选是 ONNX 封装项目发布的 `kokoro-v1.1-zh.fp16.onnx`（163528759 bytes），配套 `voices-v1.1-zh.bin`（53815880 bytes）。该模型内嵌配套词表，无需设置额外词表路径；文件大小不代表运行内存。模型和声音文件由部署者事先下载、校验并放在私有目录，程序不在运行时下载文件。

FP16 SHA256 为 `a628ea5d6fbde96d1a85f691a6a00847829937f9e488021ba2c5359bc6ea08b5`；音色包 SHA256 为 `14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd`。

已验证的备选 FP32 导出来源：<https://modelscope.cn/models/onnx-community/Kokoro-82M-v1.1-zh-ONNX>，版本 `ef182a64cb117e083b862b9d6f09fe229661e31c`，需要配套 `tokenizer.json`；其较长英文和500字符探针也生成有效音频。这些是不同导出，不能混用校验值。
模型与音色发布页：<https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1>。原始 Kokoro 权重为 Apache-2.0，ONNX 封装为 MIT。

```sh
python3.12 -m venv /tmp/kokoro-experiment-venv
/tmp/kokoro-experiment-venv/bin/python -m pip install -r requirements-kokoro-experiment.txt
```

运行独立服务，路径替换为已校验本地文件：

```sh
KOKORO_EXPERIMENT_ENABLED=1 \
KOKORO_MODEL=/absolute/private/path/kokoro-v1.1-zh.fp16.onnx \
KOKORO_VOICES=/absolute/private/path/voices-v1.1-zh.bin \
KOKORO_CACHE_DIR=/absolute/private/path/kokoro-cache \
/tmp/kokoro-experiment-venv/bin/python scripts/kokoro_worker.py --port 8877
```

社区导出没有内嵌词表，必须设置配套 `KOKORO_VOCAB`，支持 `vocab` 和 `model.vocab` 两种 JSON 形式；原有内嵌词表模型可以不设置。缓存与模型实例身份同时包含这份词表的 SHA256，不能套用另一导出的默认音素编号。

worker 固定只监听127.0.0.1，拒绝浏览器跨源直接调用。网站应通过原有登录和限流接口访问；可选服务地址仅接受 `http://127.0.0.1:<端口>`，不使用环境代理或跟随重定向。

v1.1 中文音色包的默认中文声音 `zf_001`、英文 `af_maple`，可通过 `KOKORO_ZH_VOICE`、`KOKORO_EN_VOICE` 及对应 `*_VOICES` 白名单选择本地音色文件中已有声音。声音设置不影响原设备备用音色偏好。

## 验证与接口

- `GET /health`：配置可用性与是否已加载，检查本地文件，不加载模型。
- `POST /audio`：`{language: "zh" | "en", text: "..."}`，返回 WAV、`X-TTS-Engine`、`X-TTS-Cache`。
- `POST /prepare`：`{language: "zh" | "en", texts: [...]}`，立即返回 accepted/cached/pending/failed/count；只是接收回执，不表示所有条目已合成。
- `GET /status`：排队、正在处理、完成、失败计数，不返回私人文字。

网站对应接口为 `/api/subjects/<english|chinese>/dictation/audio`、`/audio/capabilities`、`/audio/prepare`，全部要求现有用户身份；家长与关联学生共享限流。未开启实验时，能力接口返回false，英语仍用Piper，语文直接用设备普通话。

```sh
python3 -m pytest -q test_tts_kokoro.py test_dictation_audio.py test_english_dictation.py test_subject_dictation.py test_english_dictation_ui.py test_subject_dictation_ui.py
```

## 需要实测的边界

中文输入最多100字符，英语500字符，保留原有读音提示。中文使用 Misaki 1.1 发音前端；完整的“词组的单字”提示（2–8字词组，目标字只出现一次）复用词组中的对应读音，不修改全局发音词典。其他多音字仍需试听核对。英文直接得到完整音素再核对模型词表，未知音素拒绝整条并回退，避免静默漏读。超过510音素的输入完整分块合成并拼接，生成后验证完整、非静音 PCM WAV，再原子缓存。

音频缓存默认30天、最多2000文件和256 MiB，可通过 `KOKORO_CACHE_*` 调整。缓存与锁使用私有权限，文件名不含原文，服务不记录私人默写内容。

合成有软超时：加载后与每个音素分块前后检查期限；ONNX 单次原生运行无法被 Python 立刻中断。网站请求15秒后可先走备用朗读，原生计算返回后释放锁；如果仍在 worker 的软期限内，完整有效音频可留作后续缓存。超过 worker 自身软期限的结果不缓存。停止实验时取消排队并退出worker；移除网站环境变量即可恢复旧路径。

现场音质、冷启动、缓存命中延迟、峰值RSS和长句覆盖，需依据本轮实验记录评估；不得把模型文件大小或Mac速度当作现有2核2GB服务器的运行结论。
