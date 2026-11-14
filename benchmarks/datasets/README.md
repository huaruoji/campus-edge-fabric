# Campus Edge Fabric：D 的测试资料

整理日期：2026-11-14。按当前仓库的三类 workload 各准备一套资料；正式演示仍应由团队选定一个主场景。

本目录提交来源、压缩包 SHA256、逐图片清单、固定分组和本地文件检查记录。原图、标注、模型和场景二进制文件不随 Git 分发。下文所有数据路径都相对于队友自选的数据根目录，例如已被仓库忽略的 `data/test-datasets/`；不依赖某位成员的电脑路径。

## 资料分组

| 目录 | 资料 | 用途 | 压缩包体积 |
| --- | --- | --- | --- |
| `thumbnail/` | Berkeley BSDS300 images | CPU 图片缩放、转码、输入输出校验 | 约 21.2 MiB |
| `infer/` | Ultralytics COCO128 | 目标检测联调、检测框校验、吞吐对照 | 约 6.7 MiB |
| `blender/` | Blender Classroom | 场景加载和渲染输出的基础验收 | 约 67.0 MiB |

来源、文件体积和本地计算的 SHA256 见 [sources.json](sources.json)。将压缩包保存到数据根目录对应的 workload 子目录，校验 SHA256 后解压到该目录的 `extracted/`。这些 SHA256 用于确认队员持有与本次相同的文件，不代表发布者签名。

复现固定分组时，读取 [splits.json](splits.json)：将每组 `source_files` 中的图片复制到相对数据根目录的 `images_dir`；infer 图片对应的 `.txt` 标签从 `infer/extracted/coco128/labels/train2017/` 复制到同组的 `labels/`。逐图片的 SHA256 和尺寸可与 [image-manifest.json](image-manifest.json) 对照。原始资料全部保留，未配对文件不进入带标注的质量检查组。

[validation.json](validation.json) 是 2026-11-14 的本地文件检查快照，包含图片、标注语法及配对结果；不代表已经完成模型精度、多机验收或性能评测。

## 1. 图片处理：BSDS300

- 官方来源：https://www2.eecs.berkeley.edu/Research/Projects/CS/vision/bsds/
- 下载：https://www2.eecs.berkeley.edu/Research/Projects/CS/vision/bsds/BSDS300-images.tgz
- 该图片包包含 300 张原图，原始划分为训练 200 张、测试 100 张；没有下载分割标注包，因为当前 thumbnail 不需要分割标注。
- 发布页允许非商业研究和教育用途。不要把它当作无限制的商用素材，也不要直接提交大批图片到项目 Git 仓库。
- 建议先用 `size=256, fmt=webp, quality=80`；正式对照中所有设备固定同一组参数。

检查内容：

1. 每个成功任务都有能打开的输出图片。
2. 输出格式正确，长边不超过 256，并保持宽高比，容许整数像素舍入。
3. 输出对应原图，manifest 的文件名、设备和任务记录可以对上。
4. 需要逐像素对照时，先固定 Pillow 等依赖版本并生成可信单机参考输出。

## 2. 目标检测：COCO128

- 官方说明：https://docs.ultralytics.com/datasets/detect/coco128/
- 官方配置：https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/coco128.yaml
- 下载：https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip
- 图片：`infer/extracted/coco128/images/train2017/`。
- 标注：`infer/extracted/coco128/labels/train2017/`。
- 内容为 COCO train2017 的 128 张图片及相应 YOLO 检测框标注。类别采用 COCO 80 类体系，不意味着这 128 张图覆盖全部 80 类。
- **这是训练集子集。可用于工程联调、输出校验、吞吐对照，不能宣称为独立、未见过的模型泛化测试。** 若要报告正式模型精度，应另取适当的验证集，并记录模型是否见过相应数据。
- 保留包内 LICENSE 和 README；不要用其中软件许可证替代图片本身的授权条件。原图授权见 https://cocodataset.org/#termsofuse 。

本次下载后的本地配对检查发现：128 张图片与 128 份标注之间只有 **126 对同名匹配文件**。`000000000250.jpg`、`000000000508.jpg` 没有同名标注，`000000000656.txt`、`000000000659.txt` 没有同名图片。原始包保持不变；准备好的质量检查子集只使用 126 对匹配文件，另两张图片放在 `ready/infer/unlabeled/images/`。缺失标注不能直接视作“没有目标”。完整检查结果见 `validation.json`。

标注每行通常为 `class x_center y_center width height`，坐标归一化到 0—1。检测输出的框坐标应按原图尺寸换算后再比较。

建议区分两种检查：

1. **计算流程是否正确**：使用同一份模型、输入、参数和可比依赖，检查分布式输出与可信单机参考是否一致，CPU/GPU 数值差异采用预先约定的容差。
2. **模型检测效果如何**：将输出与人工标注匹配，评估预先约定的检测指标。标注是参照，不代表模型应逐框完全命中，更不能仅凭 JSON 非空认定正确。

本目录不含 `yolo11n.onnx` 等模型。模型需由 B 提供统一版本并记录 SHA256；放入各 Worker 的 `~/.cef/models/` 或指定 `--model-dir`。CPU Worker 可以执行当前 ONNX 适配器的 CPU 路径，但本次未实际运行推理。

## 3. Blender：Classroom

- 官方演示页：https://www.blender.org/download/demo-files/
- 官方下载：https://download.blender.org/demo/test/classroom.zip
- 作者：Christophe Seux；官方演示页将 Classroom 标为 CC0。
- 这是渲染场景资料，不是带目标检测标注的图片数据集。
- 先交给 B 核对 Blender 版本、外部资产依赖、CPU/GPU 配置及当前适配器兼容性，再试一帧。
- **本次只检查压缩包及文件结构，没有启动 Blender，也没有验证场景动画。**
- 解压后包含 53 个文件，其中 12 个 `.blend`。主场景是 `blender/extracted/classroom/classroom.blend`；文件头标识为 Blender 2.79，且主文件含外部 `//textures/` 路径引用。
- **当前仓库适配器使用一个输入 `.blend`，不会自动恢复这个 ZIP 的目录结构。** 先让 B 在 Blender 中处理链接资源、打包外部依赖并另存为可独立运行的 `.blend`，再验证上传到 Worker 后能正确渲染。这套资料目前是待适配素材，不是已经验证可直接上传的任务包。
- Classroom 适合作为场景加载和单帧输出测试。不要在没有实际动画或其他真实批量需求的情况下，把同一静态画面重复渲染多次当作有意义的多帧工作量。
- 正式演示若选择分布式动画渲染，让 B 提供可独立按帧渲染的动画 `.blend` 文件，并验证不同帧确实对应预期的不同画面。

## D 的执行顺序

以下是已固定的图片分组；下载后按 `splits.json` 重建即可使用：

| workload | 冒烟/联调 | 预热 | 固定测量输入 |
| --- | --- | --- | --- |
| thumbnail | `ready/thumbnail/smoke/images/`：16 张 | `ready/thumbnail/warmup/images/`：16 张 | `ready/thumbnail/benchmark/images/`：100 张，使用原始 test 分区 |
| infer | `ready/infer/smoke/images/`：8 张 | `ready/infer/warmup/images/`：8 张 | `ready/infer/benchmark/images/`：110 张，均有同名标注 |

这些是本项目的工程用途分组；infer 三组仍全部来自 COCO 训练集子集。对应检测标注在各组的 `labels/` 中，不要把 `.txt` 标签文件当作计算输入一起上传。Blender 没有自动分成三个图片目录，需要先由 B 验证场景。

1. 先用少量 thumbnail 图片跑通：网页提交、至少两台物理电脑执行、结果下载、输出校验。
2. 再按团队确定的主 workload 做正确性验收。开发/冒烟样本、预热样本、正式测量样本分开，分组见 `splits.json`。
3. 固定样本、模型/场景、参数、设备清单、并发数、软件版本和计时边界，再测每台参与设备单机及算力池。每组建议预热后重复三次。
4. 只统计正确且唯一完成的任务；保留失败、重试、结果文件和 manifest，不只保留截图。
5. 记录每轮墙钟时间和相同测试窗口内的资源利用率，再计算有效吞吐、聚合效率与相对最强单机提升。

若某轮负载不足以使多台设备实际处理任务，应增加真实、不同的输入，或者选用更合适的实际任务规模。不要仅靠把静态结果重复计算来制造吞吐数字。

原始资料保留在本地，Git 仅提交上述说明与元数据。本次准备工作没有启动 Worker、连接线上服务、运行推理或渲染，也没有修改 Coordinator、Worker 或协议代码。
