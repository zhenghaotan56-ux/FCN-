# 上游代码来源

单标的 GBM 路径生成基于 [ShrishDhuria/autocallable-pricer 的固定版本](https://github.com/ShrishDhuria/autocallable-pricer/blob/db8f59a75964727ce9fafc5e8fe327cc7f8e062a/autocallable_pricer.py)。该项目采用 MIT 许可。原作者版权和完整许可见 [LICENSE](../LICENSE)。

上游使用固定随机种子及反向配对路径。首版保留这些实现，改用实际观察日间隔，并新增不含记忆票息或独立敲入门槛的 FCN 本金及票息现金流。
