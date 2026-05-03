# wiki Domain

## Keywords
feishu, 飞书, wiki, 知识库, lark, 文档, 页面, 创建页面, 编辑页面, 写入, 移动, 删除页面

## Skills

## Tools
feishu_read, feishu_search, feishu_list, feishu_spaces, feishu_create, feishu_edit, feishu_send, feishu_comments, feishu_download, feishu_info, feishu_sheet, feishu_task, feishu_perm, feishu_chat, feishu_file, feishu_contact

## Context
飞书写入类任务的 success criteria 必须包含验证步骤：
1. feishu_create 后必须 feishu_list 验证结构 + feishu_read 验证内容
2. feishu_edit 后必须 feishu_read 验证渲染结果
3. 不要删除 wiki 页面。wiki 节点删除和 block 删除都不可靠，应提示用户去 Feishu UI 手动删除
4. 写完后不允许直接回复"已完成"，必须先验证
5. 内容更新统一走 feishu_edit（old_string/new_string 或 draft_markdown 模式）
