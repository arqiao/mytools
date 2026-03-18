import fitz  # pymupdf

def remove_watermark(input_pdf, output_pdf):
    # 打开原始 PDF
    doc = fitz.open(input_pdf)

    # 这里写你想“当作水印”的文字关键字
    watermark_keywords = ["名师", "名师汇"]  # 可以根据实际水印内容再加别的词

    for page in doc:
        text_dict = page.get_text("dict")
        blocks = text_dict.get("blocks", [])

        # 遍历文本块，查找包含水印文字的 span
        for b in blocks:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if any(k in span_text for k in watermark_keywords):
                        # span["bbox"] 是该段文字所在矩形的坐标
                        rect = fitz.Rect(span["bbox"])
                        # 在此区域添加“涂白”标注（redact annotation）
                        page.add_redact_annot(rect, fill=(1, 1, 1))

        # 对当前页面应用所有 redaction（真正把内容抹掉/盖白）
        page.apply_redactions()

    # 保存到新的 PDF 文件
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()
    print(f"去水印完成，已保存到: {output_pdf}")


if __name__ == "__main__":
    # 原始带水印的文件
    src = "4 .SAT初级词汇-水印.pdf"
    # 去水印后的新文件名
    dst = "4 .SAT初级词汇-无水印.pdf"

    remove_watermark(src, dst)

