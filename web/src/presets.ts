/**
 * 演示任务预设。
 *
 * 一条硬规则：**点卡片只把 prompt 填进输入框，绝不发送任何请求**。
 * 需要文件的预设只提示还差几个文件，同样不自动提交。
 *
 * 任务数与文件数来自 backend 的 workload 约定（一次提交一个文件 = 一个 Task，
 * Blender 按帧拆任务），仅用于界面提示，不参与任何请求参数。
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
  /** 数据集说明。 */
  dataset: string;
  /** 对应的 workload kind，用于查 GET /api/v1/workloads 的 available_on。 */
  workload: string;
  /** 预计拆出的任务数（只做展示）。 */
  taskCount: number;
  /** 提交该预设需要的文件数（只做展示与提示）。 */
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
    dataset: "BSDS300 冒烟子集（16 张）",
    workload: "thumbnail",
    taskCount: 16,
    fileCount: 16,
    paramsText: "长边 512px，WebP，质量 80",
    prompt: "用 BSDS300 的 16 张冒烟图跑一次缩略图：长边 512 像素、WebP、质量 80。跑完告诉我两台设备各完成了多少。",
  },
  {
    id: "bsds-bench",
    title: "BSDS300 对照 100 张",
    dataset: "BSDS300 对照子集（100 张）",
    workload: "thumbnail",
    taskCount: 100,
    fileCount: 100,
    paramsText: "长边 512px，WebP，质量 80（与冒烟组一致）",
    prompt: "用 BSDS300 的 100 张对照图跑缩略图，参数与冒烟组完全一致：长边 512、WebP、q80。先别提交，给我确认参数。",
  },
  {
    id: "coco-detect",
    title: "COCO128 126 对",
    dataset: "COCO128 训练集子集（126 对图）",
    workload: "infer",
    taskCount: 126,
    fileCount: 126,
    paramsText: "yolo11n，输入 640，置信度 0.25",
    prompt: "用 COCO128 的 126 对图跑目标检测：yolo11n、输入 640、置信度 0.25。说明这是训练集子集，不能当泛化测试。",
  },
  {
    id: "blender-first",
    title: "Blender Classroom 前 24 帧",
    dataset: "Blender Classroom 场景（Classroom.blend）",
    workload: "blender",
    taskCount: 24,
    fileCount: 1,
    paramsText: "前 24 帧，采样 32，分辨率 50%，每帧一个任务",
    prompt: "把 Classroom 场景渲染前 24 帧，采样 32、分辨率 50%，每帧一个任务。",
  },
];
