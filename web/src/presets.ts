/**
 * 演示任务预设。
 *
 * 两条硬规则：
 * 1. **点卡片只把 prompt 填进输入框，绝不发送任何请求**；
 * 2. 数据来源优先用已登记的数据集（spec.dataset），只有数据集未登记时才提示手动上传文件。
 *
 * 数据集必须先在任一拥有原始资料的机器上登记一次：
 *   cef dataset add <目录> --name <名字>
 * 之后所有设备都按名字引用，不再重复上传；命令路径见每条预设的 datasetCommand。
 * splits.json 里 thumbnail smoke/benchmark 的 images_dir 分别是
 * ready/thumbnail/smoke/images 与 ready/thumbnail/benchmark/images。
 */

/** workload kind → 中文名。JobChat / JobDetail 共用。 */
export const WORKLOAD_LABELS: Record<string, string> = {
  thumbnail: "缩略图",
  infer: "目标检测",
  blender: "Blender 渲染",
};

export function workloadLabel(kind: string | null | undefined): string {
  if (!kind) return "未知任务";
  return WORKLOAD_LABELS[kind] ?? kind;
}

export interface Preset {
  id: string;
  /** 卡片标题，例如「BSDS300 冒烟 16 张」。 */
  title: string;
  /** 数据集说明（人话，不带数量承诺）。 */
  dataset: string;
  /** 已登记数据集的名字；提交任务时就是 spec.dataset。 */
  datasetRef: string;
  /** 数据集未登记时，让用户在本机执行的登记命令。 */
  datasetCommand: string;
  /** 对应的 workload kind，用于查 GET /api/v1/workloads 的 available_on。 */
  workload: string;
  /** 预计拆出的任务数（数据集未登记时的估算，只做展示）。 */
  taskCount: number;
  /** 数据集未登记、改走手动上传时需要准备的文件数（只做展示与提示）。 */
  fileCount: number;
  /** 参数摘要，给卡片用。 */
  paramsText: string;
  /** 完整中文 prompt，点卡片时原样填进输入框。 */
  prompt: string;
}

export const PRESETS: readonly Preset[] = [
  {
    id: "bsds-smoke",
    title: "BSDS300 冒烟 16 张",
    dataset: "BSDS300 冒烟子集（ready/thumbnail/smoke/images）",
    datasetRef: "bsds-smoke",
    datasetCommand: "cef dataset add ./benchmarks/datasets/ready/thumbnail/smoke/images --name bsds-smoke",
    workload: "thumbnail",
    taskCount: 16,
    fileCount: 16,
    paramsText: "长边 512px，WebP，质量 80",
    prompt: "用已登记的数据集 bsds-smoke 跑一次缩略图：长边 512 像素、WebP、质量 80。跑完告诉我两台设备各完成了多少。",
  },
  {
    id: "bsds-bench",
    title: "BSDS300 对照 100 张",
    dataset: "BSDS300 对照子集（ready/thumbnail/benchmark/images）",
    datasetRef: "bsds-bench",
    datasetCommand: "cef dataset add ./benchmarks/datasets/ready/thumbnail/benchmark/images --name bsds-bench",
    workload: "thumbnail",
    taskCount: 100,
    fileCount: 100,
    paramsText: "长边 512px，WebP，质量 80（与冒烟组一致）",
    prompt: "用已登记的数据集 bsds-bench 跑缩略图，参数与冒烟组完全一致：长边 512、WebP、q80。先别提交，给我确认参数。",
  },
  {
    id: "coco-detect",
    title: "COCO128 检测",
    dataset: "COCO128 训练集子集（infer/extracted/coco128/images/train2017）",
    datasetRef: "coco128",
    datasetCommand: "cef dataset add ./benchmarks/datasets/infer/extracted/coco128/images/train2017 --name coco128",
    workload: "infer",
    taskCount: 126,
    fileCount: 126,
    paramsText: "yolo11n，输入 640，置信度 0.25",
    prompt: "用已登记的数据集 coco128 跑目标检测：yolo11n、输入 640、置信度 0.25。说明这是训练集子集，不能当泛化测试。",
  },
  {
    id: "blender-first",
    title: "Blender Classroom 前 24 帧",
    dataset: "Blender Classroom 场景（blender/extracted/classroom/classroom.blend）",
    datasetRef: "classroom",
    datasetCommand: "cef dataset add ./benchmarks/datasets/blender/extracted/classroom/classroom.blend --name classroom",
    workload: "blender",
    taskCount: 24,
    fileCount: 1,
    paramsText: "前 24 帧，采样 32，分辨率 50%，每帧一个任务",
    prompt: "用已登记的数据集 classroom 渲染 Classroom 场景前 24 帧，采样 32、分辨率 50%，每帧一个任务。",
  },
];
