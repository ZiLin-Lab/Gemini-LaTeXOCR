import sys
import os
import json
import threading
import tempfile
import re
import logging
import io
import warnings
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QComboBox, QFileDialog, QWidget, QScrollArea, QMenu, QMessageBox, QCheckBox
)
from PyQt6.QtCore import QTimer, QMetaObject, Qt, pyqtSignal, pyqtSlot, QObject
from PyQt6.QtGui import QImage, QIcon, QAction
from PIL import Image
import base64
from google import genai

# 重定向标准错误输出，捕获"共享内存读取失败"等警告
class StderrRedirector:
    def __init__(self, logger):
        self.logger = logger
        self.original_stderr = sys.stderr
        self.buffer = ""
        
    def write(self, text):
        # 写入原始stderr，保持控制台输出
        self.original_stderr.write(text)
        
        # 过滤掉"SharedMemory read faild"消息，不记录到日志
        if "SharedMemory read faild" in text:
            return
            
        # 累积文本直到遇到换行符
        self.buffer += text
        if '\n' in text:
            lines = self.buffer.split('\n')
            for line in lines[:-1]:  # 处理除最后一行外的所有行
                if line.strip():  # 忽略空行
                    self.logger.warning(f"控制台错误: {line}")
            self.buffer = lines[-1]  # 保留最后一行（可能不完整）
    
    def flush(self):
        self.original_stderr.flush()
        
    def __del__(self):
        # 恢复原始stderr
        sys.stderr = self.original_stderr

# 初始化日志模块
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ImageRecognitionApp")

# 重定向stderr
stderr_redirector = StderrRedirector(logger)
sys.stderr = stderr_redirector

# 忽略PIL的特定警告
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

# 压缩图像文件
def compress_image(image, output_path=None, max_size=(800, 600), quality=95):
    """
    压缩图像文件。
    :param image: 输入图像(PIL Image对象)或图像路径
    :param output_path: 输出图像路径，如果为None则不保存到文件
    :param max_size: 最大尺寸 (宽度, 高度)
    :param quality: 图像质量 (1-100)
    :return: 压缩后的PIL Image对象
    """
    try:
        # 如果输入是路径，则打开图像
        if isinstance(image, str):
            img = Image.open(image)
        else:
            img = image
            
        # 调整图像大小
        img.thumbnail(max_size)
        
        # 如果指定了输出路径，则保存图像
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            img.save(output_path, format="JPEG", quality=quality)
            
        return img
    except Exception as e:
        logger.error(f"压缩图像失败: {e}")
        raise

# 将图像编码为 Base64 格式
def encode_image_to_base64(image):
    """
    将图像编码为 Base64 格式。
    :param image: PIL Image对象或图像文件路径
    :return: Base64 编码后的字符串
    """
    try:
        if isinstance(image, str):
            # 如果是文件路径
            with open(image, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode("utf-8")
        else:
            # 如果是PIL Image对象
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
        logger.info(f"Base64 编码长度: {len(base64_image)}")
        return base64_image
    except Exception as e:
        logger.error(f"Base64编码失败: {e}")
        raise

# 将 QImage 转换为 PIL Image
def qimage_to_pil(qimage):
    """
    将 QImage 转换为 PIL Image 对象，使用更可靠的方法。
    :param qimage: QImage 对象
    :return: PIL Image 对象
    """
    try:
        # 首先尝试将QImage保存到临时缓冲区
        buffer = io.BytesIO()
        qimage.save(buffer, "PNG")
        buffer.seek(0)
        
        # 从缓冲区加载PIL图像
        pil_image = Image.open(buffer)
        pil_image = pil_image.convert("RGB")  # 确保图像格式一致
        return pil_image
    except Exception as e:
        logger.error(f"QImage转换为PIL Image失败(方法1): {e}")
        
        try:
            # 备用方法：如果第一种方法失败，尝试使用QImage的bits直接转换
            buffer = QImage(qimage)
            
            # 确保图像不为空
            if buffer.isNull():
                raise ValueError("QImage为空")
                
            # 获取图像格式和尺寸
            width, height = buffer.width(), buffer.height()
            if width == 0 or height == 0:
                raise ValueError(f"图像尺寸无效: {width}x{height}")
                
            # 转换为RGB格式
            if buffer.format() != QImage.Format.Format_RGB32 and buffer.format() != QImage.Format.Format_ARGB32:
                buffer = buffer.convertToFormat(QImage.Format.Format_RGB32)
            
            # 获取图像数据
            ptr = buffer.bits()
            if ptr is None:
                raise ValueError("无法获取图像数据")
                
            buffer_size = buffer.bytesPerLine() * height
            arr = bytes(ptr)[:buffer_size]
            
            # 创建PIL Image
            if buffer.format() == QImage.Format.Format_ARGB32:
                img = Image.frombytes("RGBA", (width, height), arr, "raw", "BGRA")
            else:
                img = Image.frombytes("RGB", (width, height), arr, "raw", "BGR")
                
            return img
        except Exception as e:
            logger.error(f"QImage转换为PIL Image失败(方法2): {e}")
            
            # 最后的备用方法：保存到临时文件
            try:
                temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                temp_file_path = temp_file.name
                temp_file.close()
                
                success = qimage.save(temp_file_path, "PNG")
                if not success:
                    raise ValueError("保存QImage到临时文件失败")
                    
                img = Image.open(temp_file_path)
                img = img.convert("RGB")  # 确保图像格式一致
                
                # 读取后立即删除临时文件
                os.unlink(temp_file_path)
                
                return img
            except Exception as nested_e:
                logger.error(f"QImage转换为PIL Image失败(所有方法): {nested_e}")
                raise ValueError(f"无法转换图像: {e}, {nested_e}")

# 创建一个自定义日志处理器，将日志输出到 GUI
class QTextEditLogger(logging.Handler):
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        self.setLevel(logging.INFO)
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    def emit(self, record):
        msg = self.format(record)
        # 在PyQt6中调用append方法
        self.text_edit.append(msg)

# 工作线程信号类
class WorkerSignals(QObject):
    # 定义信号
    result = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    progress = pyqtSignal(str)

# 主窗口类
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("图像内容识别工具")
        self.setGeometry(100, 100, 800, 600)
        self.last_clipboard_image_hash = None
        self.is_clipboard_monitoring_enabled = True  # 剪贴板监控开关
        self.last_clipboard_image = None  # 存储上一次的剪贴板图像
        self.processing_image = False  # 图像处理状态标志
        
        # 创建信号对象
        self.worker_signals = WorkerSignals()
        self.worker_signals.result.connect(self.update_result)
        self.worker_signals.error.connect(self.update_result)
        self.worker_signals.progress.connect(self.update_result)
        self.worker_signals.finished.connect(self.processing_finished)

        # 确保临时目录存在
        os.makedirs("temp", exist_ok=True)

        # 加载配置文件
        self.settings_file = "settings/config.json"
        self.load_settings()

        # 设置窗口图标
        icon_path = "resources/logo.ico"
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logger.warning(f"图标文件未找到: {icon_path}")

        # 创建主布局
        main_layout = QVBoxLayout()

        # API Key 输入框和保存按钮
        api_key_layout = QHBoxLayout()
        api_key_label = QLabel("API Key:")
        self.api_key_entry = QLineEdit()
        self.api_key_entry.setText(self.api_key or "")  # 默认 API Key
        self.save_api_key_button = QPushButton("保存")
        self.save_api_key_button.clicked.connect(self.save_api_key)
        api_key_layout.addWidget(api_key_label)
        api_key_layout.addWidget(self.api_key_entry)
        api_key_layout.addWidget(self.save_api_key_button)
        main_layout.addLayout(api_key_layout)

        # 模型选择下拉菜单
        model_layout = QHBoxLayout()
        model_label = QLabel("模型选择:")
        self.model_combobox = QComboBox()
        self.model_combobox.addItems(["gemini-2.0-flash"])
        self.model_combobox.setCurrentText("gemini-2.0-flash")  # 默认模型
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_combobox)
        main_layout.addLayout(model_layout)

        # 按钮布局
        button_layout = QHBoxLayout()
        self.select_button = QPushButton("选择图像文件")
        self.select_button.clicked.connect(self.select_image)
        button_layout.addWidget(self.select_button)
        
        # 添加复制结果按钮
        self.copy_button = QPushButton("复制结果")
        self.copy_button.clicked.connect(self.copy_result_to_clipboard)
        button_layout.addWidget(self.copy_button)
        
        main_layout.addLayout(button_layout)

        # 结果显示区域
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(False)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.result_text)
        main_layout.addWidget(scroll_area)

        # 日志输出区域
        log_label = QLabel("日志输出")
        main_layout.addWidget(log_label)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_scroll_area = QScrollArea()
        log_scroll_area.setWidgetResizable(True)
        log_scroll_area.setWidget(self.log_text)
        main_layout.addWidget(log_scroll_area)

        # 设置按钮
        settings_icon_path = "resources/gear.png"
        settings_action = QAction(QIcon(settings_icon_path), "设置", self)
        settings_action.triggered.connect(self.show_settings_menu)
        self.toolbar = self.addToolBar("设置")
        self.toolbar.addAction(settings_action)

        # 设置主窗口布局
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # 配置日志处理器 - 移到这里，在初始化剪贴板状态之前
        log_handler = QTextEditLogger(self.log_text)
        logger.addHandler(log_handler)

        # 启动剪贴板监控
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_clipboard_for_image)
        self.timer.start(2000)

        # 初始化剪贴板状态
        self.initialize_clipboard_state()

    @pyqtSlot(str)
    def update_result(self, text):
        """更新结果文本框的内容"""
        self.result_text.setText(text)
        
    @pyqtSlot()
    def processing_finished(self):
        """图像处理完成"""
        self.processing_image = False

    def load_settings(self):
        """加载配置文件"""
        self.api_key = ""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as file:
                    settings = json.load(file)
                    self.api_key = settings.get("api_key", "")
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")

    def save_settings(self):
        """保存配置文件"""
        settings = {"api_key": self.api_key}
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        try:
            with open(self.settings_file, "w") as file:
                json.dump(settings, file, indent=4)
            logger.info("配置文件已保存")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def save_api_key(self):
        """保存用户输入的 API Key"""
        self.api_key = self.api_key_entry.text()
        self.save_settings()
        QMessageBox.information(self, "成功", "API Key 已保存！")

    def show_settings_menu(self):
        """显示设置菜单"""
        menu = QMenu(self)
        clipboard_monitor_action = menu.addAction("设置剪贴板监控")
        clipboard_monitor_action.triggered.connect(self.show_clipboard_monitoring_dialog)
        
        # 在PyQt6中使用exec()
        menu.exec(self.toolbar.mapToGlobal(self.toolbar.pos()))

    def show_clipboard_monitoring_dialog(self):
        """显示剪贴板监控设置弹窗"""
        dialog = QMessageBox(self)
        dialog.setWindowTitle("设置剪贴板监控")
        dialog.setText("是否启用剪贴板监控？")

        checkbox = QCheckBox("", self)
        checkbox.setChecked(self.is_clipboard_monitoring_enabled)
        layout = dialog.layout()
        layout.addWidget(checkbox, 0, 0, 1, dialog.layout().columnCount())

        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Ok)

        # 在PyQt6中使用exec()
        if dialog.exec() == QMessageBox.StandardButton.Ok:
            self.toggle_clipboard_monitoring(checkbox.isChecked())

    def toggle_clipboard_monitoring(self, enable):
        """切换剪贴板监控状态"""
        self.is_clipboard_monitoring_enabled = enable
        if self.is_clipboard_monitoring_enabled:
            self.timer.start(2000)
            logger.info("剪贴板监控已启用")
        else:
            self.timer.stop()
            logger.info("剪贴板监控已禁用")

    def initialize_clipboard_state(self):
        """初始化剪贴板状态，避免误读取旧数据"""
        clipboard = QApplication.clipboard()
        if clipboard.mimeData().hasImage():
            image = clipboard.image()
            if not image.isNull():
                try:
                    # 保存到临时文件然后用PIL打开，避免共享内存问题
                    temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                    temp_file_path = temp_file.name
                    temp_file.close()
                    
                    if image.save(temp_file_path, "PNG"):
                        try:
                            pil_image = Image.open(temp_file_path).convert("RGB")
                            self.last_clipboard_image_hash = self.calculate_image_hash(pil_image)
                            self.last_clipboard_image = pil_image
                            logger.info("已初始化剪贴板图像状态")
                        finally:
                            # 确保删除临时文件
                            if os.path.exists(temp_file_path):
                                os.unlink(temp_file_path)
                except Exception as e:
                    logger.error(f"初始化剪贴板状态失败: {e}")
        else:
            # 直接记录日志，不使用QTimer延迟
            logger.info("剪贴板中没有图像，已初始化剪贴板状态")

    def select_image(self):
        """选择图像文件并处理"""
        if self.processing_image:
            QMessageBox.information(self, "提示", "正在处理图像，请稍候...")
            return
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图像文件", "", "Image Files (*.jpg *.jpeg *.png *.bmp *.gif)"
        )
        if file_path:
            api_key = self.api_key_entry.text()
            if not api_key:
                QMessageBox.warning(self, "警告", "请先输入API Key")
                return
                
            model = self.model_combobox.currentText()
            self.process_image(file_path, api_key, model)

    def copy_result_to_clipboard(self):
        """复制结果到剪贴板"""
        result_text = self.result_text.toPlainText()
        if result_text:
            # 暂时禁用剪贴板监控，避免触发自身复制的检测
            old_monitoring_state = self.is_clipboard_monitoring_enabled
            self.is_clipboard_monitoring_enabled = False
            
            # 复制文本到剪贴板
            clipboard = QApplication.clipboard()
            clipboard.setText(result_text)
            logger.info("结果已复制到剪贴板")
            
            # 使用定时器延迟恢复剪贴板监控
            QTimer.singleShot(1000, lambda: self.restore_clipboard_monitoring(old_monitoring_state))
        else:
            logger.warning("没有可复制的结果")
    
    def restore_clipboard_monitoring(self, state):
        """恢复剪贴板监控状态"""
        self.is_clipboard_monitoring_enabled = state
        logger.info(f"剪贴板监控已恢复: {'启用' if state else '禁用'}")
        
        # 重新初始化剪贴板状态，避免误处理
        self.initialize_clipboard_state()

    def check_clipboard_for_image(self):
        """检查剪贴板内容并处理新图像，优化版本无需保存未变化的图像到临时文件"""
        if not self.is_clipboard_monitoring_enabled or self.processing_image:
            return
            
        try:
            clipboard = QApplication.clipboard()
            if clipboard.mimeData().hasImage():
                image = clipboard.image()
                if not image.isNull():
                    try:
                        # 尝试将剪贴板图像保存到临时文件，然后用PIL打开
                        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                        temp_file_path = temp_file.name
                        temp_file.close()
                        
                        if image.save(temp_file_path, "PNG"):
                            try:
                                # 使用PIL打开图像
                                pil_image = Image.open(temp_file_path).convert("RGB")
                                
                                # 计算图像哈希
                                image_hash = self.calculate_image_hash(pil_image)
                                
                                if image_hash != self.last_clipboard_image_hash:
                                    # 图像发生变化，更新哈希值和保存的图像
                                    logger.info("检测到新的剪贴板图像")
                                    self.last_clipboard_image_hash = image_hash
                                    self.last_clipboard_image = pil_image
                                    
                                    # 检查API Key
                                    api_key = self.api_key_entry.text()
                                    if not api_key:
                                        logger.warning("未设置API Key，跳过处理剪贴板图像")
                                        os.unlink(temp_file_path)  # 删除临时文件
                                        return
                                        
                                    model = self.model_combobox.currentText()
                                    
                                    # 处理图像
                                    self.process_pil_image(pil_image, api_key, model)
                                else:
                                    logger.info("剪贴板图像未发生变化，跳过处理")
                            finally:
                                # 确保删除临时文件
                                if os.path.exists(temp_file_path):
                                    os.unlink(temp_file_path)
                    except Exception as e:
                        logger.error(f"处理剪贴板图像失败: {e}")
                else:
                    logger.info("剪贴板中的图像为空")
            else:
                logger.info("剪贴板中没有图像")
        except Exception as e:
            logger.error(f"检查剪贴板失败: {e}")
    
    def calculate_image_hash(self, pil_image):
        """计算图像的哈希值，更可靠的方法"""
        try:
            # 缩小图像以加快哈希计算
            small_image = pil_image.copy()
            small_image.thumbnail((100, 100))
            
            # 转换为灰度图像
            if small_image.mode != 'L':
                small_image = small_image.convert('L')
                
            # 计算图像数据的哈希值
            import hashlib
            image_data = small_image.tobytes()
            return hashlib.md5(image_data).hexdigest()
        except Exception as e:
            logger.error(f"计算图像哈希失败: {e}")
            # 回退到简单的哈希方法
            return hash(pil_image.tobytes())

    def process_image(self, image_path, api_key, model):
        """处理图像文件"""
        try:
            # 设置处理状态标志
            self.processing_image = True
            
            # 发送处理中的消息
            self.worker_signals.progress.emit("正在分析图像内容，请稍候...")
            
            def task():
                try:
                    try:
                        # 直接使用PIL打开图像，避免QImage转换问题
                        pil_image = Image.open(image_path).convert("RGB")
                        
                        # 压缩图像
                        compressed_image = compress_image(pil_image)
                        
                        # 调用API识别图像内容
                        latex_output = recognize_image_content(compressed_image, api_key, model)
                        
                        # 发送结果信号
                        self.worker_signals.result.emit(latex_output)
                    except Exception as e:
                        logger.error(f"处理图像失败: {e}")
                        # 发送错误信号
                        self.worker_signals.error.emit(f"发生错误: {e}")
                finally:
                    # 发送完成信号
                    self.worker_signals.finished.emit()
                    
            threading.Thread(target=task).start()
        except Exception as e:
            self.processing_image = False
            logger.error(f"启动图像处理线程失败: {e}")
            self.worker_signals.error.emit(f"发生错误: {e}")

    def process_pil_image(self, pil_image, api_key, model):
        """直接处理PIL图像对象"""
        try:
            # 设置处理状态标志
            self.processing_image = True
            
            # 发送处理中的消息
            self.worker_signals.progress.emit("正在分析图像内容，请稍候...")
            
            def task():
                try:
                    try:
                        # 压缩图像
                        compressed_image = compress_image(pil_image)
                        
                        # 调用API识别图像内容
                        latex_output = recognize_image_content(compressed_image, api_key, model)
                        
                        # 发送结果信号
                        self.worker_signals.result.emit(latex_output)
                    except Exception as e:
                        # 发送错误信号
                        self.worker_signals.error.emit(f"发生错误: {e}")
                finally:
                    # 发送完成信号
                    self.worker_signals.finished.emit()
                    
            threading.Thread(target=task).start()
        except Exception as e:
            self.processing_image = False
            logger.error(f"启动图像处理线程失败: {e}")
            self.worker_signals.error.emit(f"发生错误: {e}")

# 处理 LaTeX 输出结果
def process_latex_output(latex_output):
    """
    处理 LaTeX 输出结果，去除多余的 Markdown 代码块标记。
    同时将 \\[ 和 \\] 替换为 $$，将 \\( 和 \\) 替换为 $。
    将 equation* 和 align* 环境转换为 $$ 格式。
    :param latex_output: 原始 LaTeX 输出字符串
    :return: 处理后的 LaTeX 字符串
    """
    try:
        # 去除首尾的多余 Markdown 代码块标记
        latex_output = latex_output.strip()
        if latex_output.startswith("```latex"):
            latex_output = latex_output[8:].strip()
        elif latex_output.startswith("```"):
            latex_output = latex_output[3:].strip()
        if latex_output.endswith("```"):
            latex_output = latex_output[:-3].strip()

        # 将 \\[ 和 \\] 替换为 $$，将 \\( 和 \\) 替换为 $
        latex_output = latex_output.replace(r'\[', '$$').replace(r'\]', '$$')
        latex_output = latex_output.replace(r'\(', '$').replace(r'\)', '$')
        
        # 处理 equation* 和 align* 环境
        
        # 处理 equation* 环境
        equation_pattern = re.compile(r'\\begin\{equation\*\}(.*?)\\end\{equation\*\}', re.DOTALL)
        latex_output = equation_pattern.sub(r'$$ \1 $$', latex_output)
        
        # 处理 align* 环境
        align_pattern = re.compile(r'\\begin\{align\*\}(.*?)\\end\{align\*\}', re.DOTALL)
        latex_output = align_pattern.sub(r'$$ \1 $$', latex_output)

        return latex_output
    except Exception as e:
        logger.error(f"处理LaTeX输出失败: {e}")
        return latex_output  # 出错时返回原始输出

# 调用 Gemini API 识别图像内容
def recognize_image_content(image, api_key, model):
    """
    调用Gemini API识别图像内容
    :param image: PIL Image对象或图像文件路径
    :param api_key: API密钥
    :param model: 模型名称
    :return: 处理后的LaTeX字符串
    """
    try:
        client = genai.Client(api_key=api_key)
        
        # 压缩图像并编码为Base64
        if isinstance(image, str):
            # 如果是文件路径，先压缩
            compressed_image = compress_image(image)
            base64_image = encode_image_to_base64(compressed_image)
        else:
            # 如果已经是PIL Image对象，直接编码
            # 确保使用副本，避免修改原始图像
            compressed_image = compress_image(image.copy())
            base64_image = encode_image_to_base64(compressed_image)
        
        # 调用API
        response = client.models.generate_content(
            model=model,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": "请以LaTeX代码格式输出图像中的所有内容，不需要documentclass声明。请确保微分符号d、虚数单位i和欧拉常数e为正体，使用\\mathrm{}包裹，对于加粗的符号使用\\bm{}包裹，不要使用\\mathbf。请注意分辨行内公式与行间公式，行间公式不要使用有编号的公式环境。对于行列式请使用vmatrix环境，对于矩阵请使用pmatrix环境。"},
                        {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
                    ]
                }
            ]
        )

        raw_latex_output = response.text
        processed_latex_output = process_latex_output(raw_latex_output)
        return processed_latex_output
    except Exception as e:
        logger.error(f"API 调用失败: {e}")
        raise

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        # 在PyQt6中使用app.exec()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"程序启动失败: {e}")
        # 恢复原始stderr
        if 'stderr_redirector' in globals() and hasattr(stderr_redirector, 'original_stderr'):
            sys.stderr = stderr_redirector.original_stderr
        raise 