import { ElMessage, ElMessageBox } from "element-plus";

export type MenuAction = {
  key: string;
  label: string;
  disabled?: boolean;
  danger?: boolean;
  title?: string;
};

export function toastError(err: unknown) {
  const msg =
    err && typeof err === "object" && "message" in err
      ? String((err as { message?: unknown }).message || err)
      : String(err);
  ElMessage.error(msg);
}

export function toastSuccess(msg: string) {
  ElMessage.success(msg);
}

export function toastWarning(msg: string) {
  ElMessage.warning(msg);
}

export async function confirmAction(message: string, title = "确认"): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      type: "warning",
      confirmButtonText: "确认",
      cancelButtonText: "取消",
    });
    return true;
  } catch {
    return false;
  }
}

export function chipTagType(tone: string): "success" | "warning" | "danger" | "info" | "primary" {
  switch (tone) {
    case "pass":
    case "ok":
    case "READY":
      return "success";
    case "fail":
    case "GATE_FAILED":
      return "danger";
    case "warn":
    case "abort":
    case "OTHER":
    case "issue":
      return "warning";
    case "used":
      return "info";
    case "running":
    case "progress":
    case "IN_PROGRESS":
      return "primary";
    default:
      return "info";
  }
}
