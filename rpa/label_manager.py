"""
RPA自动化工具 - 标签管理模块

提供流程中标签节点的管理功能：
- 收集所有标签节点
- 验证跳转目标标签是否存在
- 获取标签在流程中的位置
"""

from typing import Any, Dict, List, Optional


class LabelManager:
    """
    标签管理工具类。

    用于管理流程中的标签节点，支持跳转节点的目标定位。
    所有方法均为静态方法。
    """

    @staticmethod
    def collect_all_labels(
        step_list: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """
        收集所有标签节点的名称和所在位置。

        递归遍历整个流程树，包括分支和循环节点的子节点。

        Args:
            step_list: 步骤列表（流程数据）

        Returns:
            字典，键为标签名称，值为包含以下信息的字典：
            - 'list': 标签所在的列表引用
            - 'index': 标签在列表中的索引
            - 'path': 标签在流程树中的路径（如 '0.2.true.1'）
        """
        labels: Dict[str, Dict[str, Any]] = {}

        def traverse(lst: List[Dict], parent_path: str = "") -> None:
            for idx, step in enumerate(lst):
                path = f"{parent_path}.{idx}" if parent_path else str(idx)
                if step['type'] == '标签':
                    label_name = step['params'].get('label_name', '').strip()
                    if label_name:
                        labels[label_name] = {
                            'list': lst,
                            'index': idx,
                            'path': path,
                        }
                # 递归遍历子节点
                if 'true' in step:
                    traverse(step['true'], f"{path}.true")
                if 'false' in step:
                    traverse(step['false'], f"{path}.false")
                if 'body' in step:
                    traverse(step['body'], f"{path}.body")

        traverse(step_list)
        return labels

    @staticmethod
    def validate_jump(
        step_list: List[Dict[str, Any]],
        target_label: str,
    ) -> bool:
        """
        验证跳转目标标签是否存在。

        Args:
            step_list: 步骤列表（流程数据）
            target_label: 目标标签名称

        Returns:
            True 表示标签存在，False 表示不存在
        """
        labels = LabelManager.collect_all_labels(step_list)
        return target_label.strip() in labels

    @staticmethod
    def get_label_position(
        step_list: List[Dict[str, Any]],
        target_label: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取标签所在的列表和索引。

        Args:
            step_list: 步骤列表（流程数据）
            target_label: 目标标签名称

        Returns:
            包含 'list', 'index', 'path' 的字典，未找到时返回 None
        """
        labels = LabelManager.collect_all_labels(step_list)
        return labels.get(target_label.strip())
