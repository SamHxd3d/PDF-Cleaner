# -*- coding: utf-8 -*-
"""
PDF 清理工具
功能：
1. 批量选择、拖拽导入 PDF
2. 可配置：去打开密码 / 去权限锁 / 签名处理（保留签章或完全删除）
3. 输出目录可选：原文件夹 / 程序同目录 / 自定义
4. 输出 *_cleaned.pdf，不覆盖原文件

依赖：
    python -m pip install -U pikepdf tkinterdnd2
"""

import os
import sys
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

import pikepdf
from pikepdf import Pdf, Name

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False


# ============================================================
# PDF 核心处理
# ============================================================

def open_pdf(input_path, password=""):
    return Pdf.open(input_path, password=password)


def remove_sig_data_keep_appearance(pdf):
    """只删 AcroForm 里的 Sig 签名字段，页面 Widget/印章图片保留。返回删除数。"""
    removed = 0
    root = pdf.Root

    def filter_fields(fields):
        nonlocal removed
        result = []
        for field in fields:
            try:
                if field.get("/FT") == Name.Sig:
                    removed += 1
                    continue
                if "/Kids" in field:
                    field.Kids = pikepdf.Array(filter_fields(field.Kids))
                result.append(field)
            except Exception:
                result.append(field)
        return result

    if "/AcroForm" in root:
        acro = root.AcroForm
        if "/Fields" in acro:
            try:
                root.AcroForm.Fields = pikepdf.Array(filter_fields(acro.Fields))
            except Exception:
                traceback.print_exc()

    try:
        if "/AcroForm" in root:
            fields = root.AcroForm.get("/Fields", [])
            if len(fields) == 0:
                del root["/AcroForm"]
    except Exception:
        pass
    return removed


def remove_all_signatures_and_widgets(pdf):
    """删除 Sig 签名字段 + 页面上的签名 Widget 批注（印章图片一起消失）。"""
    removed = 0
    root = pdf.Root
    sig_objgens = set()

    def obj_key(obj):
        try:
            objgen = obj.objgen
            if objgen and objgen != (0, 0):
                return tuple(objgen)
        except Exception:
            pass
        return None

    def collect_sig_fields(fields):
        for field in fields:
            try:
                ft = field.get("/FT")
                if ft == Name.Sig:
                    key = obj_key(field)
                    if key:
                        sig_objgens.add(key)
                elif "/Kids" in field:
                    collect_sig_fields(field.Kids)
            except Exception:
                continue

    def filter_fields(fields):
        result = []
        for field in fields:
            try:
                if field.get("/FT") == Name.Sig:
                    continue
                if "/Kids" in field:
                    field.Kids = pikepdf.Array(filter_fields(field.Kids))
                result.append(field)
            except Exception:
                result.append(field)
        return result

    if "/AcroForm" in root:
        acro = root.AcroForm
        if "/Fields" in acro:
            try:
                collect_sig_fields(acro.Fields)
                root.AcroForm.Fields = pikepdf.Array(filter_fields(acro.Fields))
            except Exception:
                traceback.print_exc()

    for page in pdf.pages:
        try:
            if "/Annots" not in page:
                continue
            result = []
            for annot in page.Annots:
                try:
                    subtype = annot.get("/Subtype")
                    ft = annot.get("/FT")
                    parent = annot.get("/Parent")
                    parent_key = obj_key(parent) if parent is not None else None
                    own_key = obj_key(annot)
                    is_sig_widget = (
                        subtype == Name.Widget
                        and (
                            ft == Name.Sig
                            or own_key in sig_objgens
                            or parent_key in sig_objgens
                        )
                    )
                    if is_sig_widget:
                        removed += 1
                        continue
                except Exception:
                    pass
                result.append(annot)
            page.Annots = pikepdf.Array(result)
        except Exception:
            traceback.print_exc()

    try:
        if "/AcroForm" in root:
            fields = root.AcroForm.get("/Fields", [])
            if len(fields) == 0:
                del root["/AcroForm"]
    except Exception:
        pass
    return removed


def get_program_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_default_output_folder():
    out = os.path.join(get_program_dir(), "PDF Cleaned")
    os.makedirs(out, exist_ok=True)
    return out


def make_output_path(input_path, output_dir):
    stem = os.path.splitext(os.path.basename(input_path))[0]
    candidate = os.path.join(output_dir, stem + "_cleaned.pdf")
    if not os.path.exists(candidate):
        return candidate
    index = 2
    while True:
        candidate = os.path.join(output_dir, f"{stem}_cleaned_{index}.pdf")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def process_pdf(input_path, password, output_dir, settings):
    """
    settings: dict，包含
      - remove_password: bool
      - remove_perms: bool
      - sig_action: 0=不处理, 1=去签名留签章, 2=完全去签名
    """
    pdf = open_pdf(input_path, password)
    try:
        sig_log = ""
        if settings["sig_action"] == 1:
            n = remove_sig_data_keep_appearance(pdf)
            sig_log = f"已移除 {n} 个签名字段（签章图片保留）"
        elif settings["sig_action"] == 2:
            n = remove_all_signatures_and_widgets(pdf)
            sig_log = f"已移除 {n} 个签名相关对象（含印章图）"
        else:
            sig_log = "未处理签名"

        output_path = make_output_path(input_path, output_dir)

        # 只要勾了"去打开密码"或"去权限锁"，就无加密输出（pikepdf不设encryption即同时去除两者）
        no_encrypt = settings["remove_password"] or settings["remove_perms"]
        if no_encrypt:
            pdf.save(output_path, linearize=True)
        else:
            # 都不勾：保留原加密参数输出（需已有密码）
            try:
                pdf.save(output_path, linearize=True)
            except Exception:
                pdf.save(output_path)

        return output_path, sig_log
    finally:
        pdf.close()


# ============================================================
# 设置窗口
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, current_settings):
        super().__init__(parent)
        self.title("设置")
        self.geometry("500x560")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        
        self.result = None
        s = current_settings

        # ---- 输出文件夹 ----
        frm1 = tk.LabelFrame(self, text="输出文件夹", padx=10, pady=8)
        frm1.pack(fill=tk.X, padx=10, pady=8)

        self.out_mode = tk.IntVar(value=s["output_mode"])
        tk.Radiobutton(frm1, text="原文件所在文件夹",
                       variable=self.out_mode, value=0,
                       command=self._on_out_mode).pack(anchor="w")
        tk.Radiobutton(frm1, text="程序同目录 PDF Cleaned 文件夹",
                       variable=self.out_mode, value=1,
                       command=self._on_out_mode).pack(anchor="w")
        row = tk.Frame(frm1)
        row.pack(fill=tk.X, pady=2)
        tk.Radiobutton(row, text="自定义：",
                       variable=self.out_mode, value=2,
                       command=self._on_out_mode).pack(side=tk.LEFT)
        self.custom_path_var = tk.StringVar(value=s["custom_output_dir"])
        self.path_entry = tk.Entry(row, textvariable=self.custom_path_var, width=32)
        self.path_entry.pack(side=tk.LEFT, padx=2)
        self.browse_btn = tk.Button(row, text="浏览…", command=self._browse)
        self.browse_btn.pack(side=tk.LEFT)

        self._on_out_mode()

        # ---- 修改内容勾选 ----
        frm2 = tk.LabelFrame(self, text="清理内容", padx=10, pady=8)
        frm2.pack(fill=tk.X, padx=10, pady=8)

        self.var_all = tk.BooleanVar()
        self.var_password = tk.BooleanVar(value=s["remove_password"])
        self.var_perms = tk.BooleanVar(value=s["remove_perms"])
        self.var_sig_keep = tk.BooleanVar(value=(s["sig_action"] == 1))
        self.var_sig_full = tk.BooleanVar(value=(s["sig_action"] == 2))

        self.chk_all = tk.Checkbutton(frm2, text="全部",
                                      variable=self.var_all,
                                      command=self._toggle_all)
        self.chk_all.pack(anchor="w")

        self.chk_password = tk.Checkbutton(frm2, text="去除打开密码",
                                           variable=self.var_password,
                                           command=self._on_item_change)
        self.chk_password.pack(anchor="w", padx=15)

        self.chk_perms = tk.Checkbutton(frm2, text="去除权限锁（禁止复制/打印/编辑等）",
                                        variable=self.var_perms,
                                        command=self._on_item_change)
        self.chk_perms.pack(anchor="w", padx=15)

        # ---- 签名处理二选一（单独框起来）----
        sig_frame = tk.LabelFrame(frm2, text="数字签名处理（二选一）",
                                  padx=8, pady=6, fg="#d62828")
        sig_frame.pack(fill=tk.X, padx=15, pady=(6, 4))

        tk.Label(sig_frame,
                 text="文件留档或上传请选第一项 ；需要重新签章请选第二项。",
                 fg="#666", wraplength=380, justify=tk.LEFT
                 ).pack(anchor="w", pady=(0, 4))

        self.chk_sig_keep = tk.Checkbutton(
            sig_frame,
            text="去除签名数据，保留印章图片（推荐）",
            variable=self.var_sig_keep,
            command=self._on_sig_keep_toggle)
        self.chk_sig_keep.pack(anchor="w")

        self.chk_sig_full = tk.Checkbutton(
            sig_frame,
            text="完全去除签名（连印章图片一起删除，慎用）",
            variable=self.var_sig_full,
            command=self._on_sig_full_toggle)
        self.chk_sig_full.pack(anchor="w", pady=(2, 0))

        self._refresh_all_state()

        # ---- 按钮 ----
        btn_row = tk.Frame(self)
        btn_row.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(btn_row, text="取消", width=10, command=self.destroy).pack(side=tk.RIGHT, padx=4)
        tk.Button(btn_row, text="保存", width=10, bg="#2478d4", fg="white",
                  command=self._save).pack(side=tk.RIGHT, padx=4)

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - 500) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - 560) // 2
        self.geometry(f"+{max(x,0)}+{max(y,0)}")

    # ---- 输出文件夹 ----
    def _on_out_mode(self):
        mode = self.out_mode.get()
        state_normal = tk.NORMAL if mode == 2 else tk.DISABLED
        self.path_entry.config(state=state_normal)
        self.browse_btn.config(state=state_normal)

    def _browse(self):
        d = filedialog.askdirectory(parent=self, title="选择输出文件夹")
        if d:
            self.custom_path_var.set(d)

    # ---- 勾选联动 ----
    def _toggle_all(self):
        if self.var_all.get():
            self.var_password.set(True)
            self.var_perms.set(True)
            self.var_sig_keep.set(True)
            self.var_sig_full.set(False)
        else:
            self.var_password.set(False)
            self.var_perms.set(False)
            self.var_sig_keep.set(False)
            self.var_sig_full.set(False)

    def _on_item_change(self):
        self._refresh_all_state()

    def _on_sig_keep_toggle(self):
        if self.var_sig_keep.get():
            self.var_sig_full.set(False)
        self._refresh_all_state()

    def _on_sig_full_toggle(self):
        if self.var_sig_full.get():
            self.var_sig_keep.set(False)
        self._refresh_all_state()

    def _refresh_all_state(self):
        # "全部"的定义：去密码 + 去权限 + (去签名留签章 或 完全去签名)
        all_checked = (
            self.var_password.get()
            and self.var_perms.get()
            and (self.var_sig_keep.get() or self.var_sig_full.get())
        )
        self.var_all.set(all_checked)

    # ---- 保存 ----
    def _save(self):
        mode = self.out_mode.get()
        custom_dir = self.custom_path_var.get().strip()
        if mode == 2 and not custom_dir:
            messagebox.showwarning("提示", "请选择自定义输出文件夹", parent=self)
            return
        if mode == 2:
            os.makedirs(custom_dir, exist_ok=True)

        if self.var_sig_keep.get():
            sig_action = 1
        elif self.var_sig_full.get():
            sig_action = 2
        else:
            sig_action = 0

        self.result = {
            "output_mode": mode,
            "custom_output_dir": custom_dir,
            "remove_password": self.var_password.get(),
            "remove_perms": self.var_perms.get(),
            "sig_action": sig_action,
        }
        self.destroy()


# ============================================================
# 主界面
# ============================================================

def show_startup_notice(root):
    notice = (
        "本软件使用须知 -by Sam\n\n"
        "1. 本软件用于清理 PDF：移除权限限制、处理数字签名结构。\n\n"
        "2. 移除打开密码的前提是您已知该 PDF 的正确打开密码；"
        "本软件不具备暴力破解能力。\n\n"
        "3. 处理后文件不再保留原数字签名的验签效力，仅作为提交副本使用。"
        "请妥善保留原始 PDF。\n\n"
        "4. 请确认您有权修改该文件。因非法使用造成的后果由使用者自行承担。\n\n"
        "点击“我已知晓”继续使用。"
    )
    messagebox.showwarning("PDF 清理工具 - 使用须知", notice, parent=root)


class PdfCleanerApp:

    def __init__(self, root):
        self.root = root
        try:
            self.root.iconbitmap("")
        except Exception:
            pass
        self.root.title("PDF 清理工具 · v1.3   Sam制作")
        self.root.geometry("760x600")
        self.root.minsize(600, 480)

        self.files = []
        self.index = 0
        self.ok_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self.busy = False

        # 默认设置
        self.settings = {
            "output_mode": 0,          # 0=原文件夹 1=程序同目录 2=自定义
            "custom_output_dir": "",
            "remove_password": True,
            "remove_perms": True,
            "sig_action": 1,           # 1=去签名留签章
        }

        # 顶部按钮
        toolbar = tk.Frame(root)
        toolbar.pack(fill=tk.X, padx=8, pady=6)

        self.add_button = tk.Button(toolbar, text="添加 PDF…",
                                    width=10, command=self.add_files)
        self.add_button.pack(side=tk.LEFT, padx=2)

        self.clear_button = tk.Button(toolbar, text="清空列表",
                                      width=8, command=self.clear_list)
        self.clear_button.pack(side=tk.LEFT, padx=2)

        self.settings_button = tk.Button(toolbar, text="设置…",
                                        width=8, command=self.open_settings)
        self.settings_button.pack(side=tk.LEFT, padx=10)
        self.default_button = tk.Button(toolbar, text="恢复默认",width=8, command=self.restore_default)
                                        
        self.default_button.pack(side=tk.LEFT, padx=2)

        self.start_button = tk.Button(toolbar, text="开始处理",
                                     width=10, bg="#2478d4", fg="white",
                                     command=self.start_process)
        self.start_button.pack(side=tk.RIGHT, padx=2)

        # 当前设置摘要标签（红色大字 + 闪烁提醒）
        hint_frame = tk.LabelFrame(root, text="当前设置", padx=8, pady=6)
        hint_frame.pack(fill=tk.X, padx=8, pady=(2, 4))
        self.settings_hint = tk.Label(
            hint_frame, text="", fg="#d62828", anchor="w",
            font=("", 12, "bold"), justify=tk.LEFT
        )
        self.settings_hint.pack(fill=tk.X)
        self._flash_steps = 0
        self._update_settings_hint()
        self.root.after(300, self._flash_settings_hint)  # 启动时闪烁

        # 拖拽区域
        self.drop_frame = tk.LabelFrame(root, text="PDF 文件列表", padx=6, pady=6)
        self.drop_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.hint = tk.Label(self.drop_frame,
                             text="将 PDF 文件拖到此区域，或点击“添加 PDF…”\n支持一次拖入多个文件",
                             fg="#666666", justify=tk.CENTER, pady=4)
        self.hint.pack(fill=tk.X)

        list_frame = tk.Frame(self.drop_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED,
                                  yscrollcommand=scrollbar.set, height=8)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)

        tk.Label(root, text="处理日志").pack(anchor="w", padx=8, pady=(3, 0))
        self.log_box = ScrolledText(root, height=8, wrap=tk.WORD)
        self.log_box.pack(fill=tk.BOTH, padx=8, pady=4)

        self.status = tk.StringVar(value="就绪")
        tk.Label(root, textvariable=self.status, anchor="w",
                 relief=tk.SUNKEN).pack(fill=tk.X, side=tk.BOTTOM)

        if DND_AVAILABLE:
            self.enable_drag_drop()
        else:
            self.log("提示：未检测到 tkinterdnd2，拖拽不可用。")

    # --------------------------------------------------------
    def log(self, text):
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)

    def set_busy(self, busy):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.add_button.config(state=state)
        self.clear_button.config(state=state)
        self.start_button.config(state=state)
        self.settings_button.config(state=state)

    def _update_settings_hint(self):
        s = self.settings
        out_map = {0: "原文件所在文件夹", 1: "程序同目录 PDF_Cleaned", 2: "自定义"}
        sig_map = {0: "不处理签名", 1: "去签名·留签章", 2: "完全去签名"}
        line1 = f"输出位置：{out_map[s['output_mode']]}"
        if s["output_mode"] == 2:
            line1 += f" （{s['custom_output_dir']}）"
        opts = []
        if s["remove_password"]:
            opts.append("去除打开密码")
        if s["remove_perms"]:
            opts.append("去除权限锁")
        opts.append(sig_map[s["sig_action"]])
        line2 = "清理内容：" + "、".join(opts)
        self.settings_hint.config(text=line1 + "\n" + line2)

    def _flash_settings_hint(self, times=8):
        """设置摘要区域闪烁，吸引注意。"""
        self._flash_steps = times
        self._flash_tick()

    def _flash_tick(self):
        if self._flash_steps <= 0:
            self.settings_hint.config(bg=self.root.cget("bg"))
            return
        self._flash_steps -= 1
        if self._flash_steps % 2 == 0:
            self.settings_hint.config(bg="#fffe00")   # 淡黄高亮
        else:
            self.settings_hint.config(bg=self.root.cget("bg"))
        self.root.after(220, self._flash_tick)

    # --------------------------------------------------------
    def open_settings(self):
        if self.busy:
            return
        dlg = SettingsDialog(self.root, self.settings)
        self.root.wait_window(dlg)
        if dlg.result:
            self.settings = dlg.result
            self._update_settings_hint()
            self._flash_settings_hint()
            self.log("设置已更新。")

    def restore_default(self):
        if self.busy:
            return
        if not messagebox.askyesno("恢复默认", "确定要恢复默认设置吗？"):
            return
        self.settings = {
            "output_mode": 0,
            "custom_output_dir": "",
            "remove_password": True,
            "remove_perms": True,
            "sig_action": 1,
        }
        self._update_settings_hint()
        self._flash_settings_hint()
        self.log("已恢复默认设置。")

    # --------------------------------------------------------
    def enable_drag_drop(self):
        targets = [self.root, self.drop_frame, self.hint, self.listbox]
        ok = False
        for w in targets:
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self.on_drop)
                ok = True
            except Exception as exc:
                self.log(f"拖拽注册失败: {exc}")
        if ok:
            self.log("程序已启动，使用默认设置，去除签名但保留印章图样。")
            self.log("请注意，签名不是指签名文字，而是指用于PDF保护的电子签名。")

    def on_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
            count = sum(1 for p in paths if self._add_one(p))
            self.status.set(f"已导入 {count} 个 PDF")
            self.log(f"拖拽导入：新增 {count} 个 PDF")
        except Exception as exc:
            self.log(f"拖拽解析失败：{exc}")
        return event.action

    # --------------------------------------------------------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf *.PDF")])
        count = sum(1 for p in paths if self._add_one(p))
        self.log(f"文件选择：新增 {count} 个 PDF")

    def _add_one(self, path):
        path = os.path.normpath(path)
        if not path.lower().endswith(".pdf") or not os.path.isfile(path):
            return False
        if path in self.files:
            return False
        self.files.append(path)
        self.listbox.insert(tk.END, path)
        return True

    def clear_list(self):
        if self.busy:
            return
        self.files.clear()
        self.listbox.delete(0, tk.END)
        self.log_box.delete("1.0", tk.END)
        self.status.set("就绪")

    # --------------------------------------------------------
    def _resolve_output_dir(self, input_path):
        mode = self.settings["output_mode"]
        if mode == 0:
            return os.path.dirname(input_path)
        if mode == 1:
            return get_default_output_folder()
        return self.settings["custom_output_dir"]

    def start_process(self):
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("提示", "请先添加 PDF 文件。")
            return
        self.set_busy(True)
        self.index = 0
        self.ok_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self.batch_files = list(self.files)

        self.log("\n========== 开始批量处理 ==========")
        s = self.settings
        out_map = {0: "原文件夹", 1: "程序同目录 PDF Cleaned", 2: "自定义"}
        sig_map = {0: "不处理签名", 1: "去签名但留印章", 2: "完全去签名"}
        self.log(f"输出位置：{out_map[s['output_mode']]}")
        opts = []
        if s["remove_password"]:
            opts.append("去打开密码")
        if s["remove_perms"]:
            opts.append("去权限锁")
        opts.append(sig_map[s["sig_action"]])
        self.log("清理内容：" + "、".join(opts))
        self.status.set("正在检查 PDF…")
        self.root.after(10, self.process_next)

    def ask_password(self, path):
        while True:
            pwd = simpledialog.askstring(
                "需要打开密码",
                f"此 PDF 无法使用空密码打开：\n\n{os.path.basename(path)}\n\n"
                "请输入打开密码。\n取消则跳过此文件。",
                show="*", parent=self.root)
            if pwd is None:
                return None
            try:
                pdf = open_pdf(path, pwd)
                pdf.close()
                return pwd
            except pikepdf.PasswordError:
                if not messagebox.askretrycancel(
                        "密码错误", "密码不正确，是否重新输入？\n\n取消则跳过。",
                        parent=self.root):
                    return None
            except Exception as exc:
                self.log(f"密码验证错误：{exc}")
                return None

    def process_next(self):
        if self.index >= len(self.batch_files):
            self.finish_batch()
            return

        path = self.batch_files[self.index]
        self.index += 1
        self.status.set(f"处理中 ({self.index}/{len(self.batch_files)}): "
                        f"{os.path.basename(path)}")
        self.log(f"\n→ {path}")

        password = ""
        try:
            t = open_pdf(path, "")
            t.close()
            self.log("  空密码可打开。")
        except pikepdf.PasswordError:
            self.log("  需要打开密码。")
            password = self.ask_password(path)
            if password is None:
                self.skip_count += 1
                self.log("  已跳过。")
                self.root.after(10, self.process_next)
                return
        except Exception as exc:
            self.fail_count += 1
            self.log(f"  ❌ 无法读取：{exc}")
            self.root.after(10, self.process_next)
            return

        try:
            out_dir = self._resolve_output_dir(path)
            output_path, sig_log = process_pdf(path, password, out_dir, self.settings)
            self.ok_count += 1
            self.log("  ✅ 处理成功")
            self.log(f"  {sig_log}")
            self.log(f"  输出：{output_path}")
        except pikepdf.PasswordError:
            self.fail_count += 1
            self.log("  ❌ 密码验证失败。")
        except Exception as exc:
            self.fail_count += 1
            self.log(f"  ❌ 处理失败：{exc}")
            self.log(traceback.format_exc())

        self.root.after(10, self.process_next)

    def finish_batch(self):
        self.set_busy(False)
        summary = (f"成功：{self.ok_count} 个\n"
                   f"失败：{self.fail_count} 个\n"
                   f"跳过：{self.skip_count} 个")
        self.status.set(f"完成：成功 {self.ok_count}，失败 {self.fail_count}，跳过 {self.skip_count}")
        self.log("\n========== 批量处理结束 ==========")
        self.log(summary)
        messagebox.showinfo("处理完成", summary, parent=self.root)
        self.files.clear()
        self.listbox.delete(0, tk.END)


def main():
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    root.withdraw()
    show_startup_notice(root)
    root.deiconify()
    PdfCleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
