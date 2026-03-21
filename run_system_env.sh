#!/bin/bash
# 在系统Python环境下运行量化系统
# 解决NumPy版本冲突问题

# 设置环境变量
export PYTHONPATH="/usr/lib/python3/dist-packages:$PYTHONPATH"

# 添加quant_system路径
export PYTHONPATH="/root/.openclaw/workspace/quant_system:$PYTHONPATH"
export PYTHONPATH="/root/.openclaw/workspace/quant_system/real_factors:$PYTHONPATH"
export PYTHONPATH="/root/.openclaw/workspace/quant_system/walkforward:$PYTHONPATH"

echo "=== 在系统Python环境下运行量化系统 ==="
echo "PYTHONPATH: $PYTHONPATH"

# 检查Python版本和包
python3 -c "
import sys
print(f'Python {sys.version}')
import numpy as np
print(f'NumPy {np.__version__}')
import pandas as pd
print(f'Pandas {pd.__version__}')
try:
    import statsmodels.api as sm
    print('statsmodels ✓')
except:
    print('statsmodels ✗')
try:
    from sklearn.ensemble import GradientBoostingRegressor
    print('scikit-learn ✓')
except:
    print('scikit-learn ✗')
try:
    import akshare as ak
    print(f'AKShare {ak.__version__} ✓')
except:
    print('AKShare ✗')
"

# 根据参数运行不同的模块
if [ "$1" = "multifactor" ]; then
    echo -e "\n运行多因子回归测试..."
    cd /root/.openclaw/workspace/quant_system
    python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
from multi_factor_regression import test_multi_factor_regression
test_multi_factor_regression()
"
elif [ "$1" = "alpha" ]; then
    echo -e "\n运行Alpha预测器测试..."
    cd /root/.openclaw/workspace/quant_system
    python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system')
from alpha_predictor import test_alpha_predictor
test_alpha_predictor()
"
elif [ "$1" = "walkforward" ]; then
    echo -e "\n运行Walk-forward回测..."
    cd /root/.openclaw/workspace/quant_system
    python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system/walkforward')
from walkforward_backtester import WalkForwardBacktester, WalkForwardConfig
config = WalkForwardConfig(train_years=1, test_months=3, step_months=2)
tester = WalkForwardBacktester(config)
print('Walk-forward回测器初始化成功')
"
elif [ "$1" = "factors" ]; then
    echo -e "\n测试真实因子管理器..."
    cd /root/.openclaw/workspace/quant_system
    python3 -c "
import sys
sys.path.append('/root/.openclaw/workspace/quant_system/real_factors')
from real_factor_manager import RealFactorManager
import pandas as pd
import numpy as np

fm = RealFactorManager()
print(f'因子管理器: {len(fm.factors)}个因子')
print(f'技术因子: {fm.category_stats[\"technical\"]}')
print(f'基本面因子: {fm.category_stats[\"fundamental\"]}')
"
elif [ "$1" = "integrated" ]; then
    echo -e "\n运行集成测试..."
    cd /root/.openclaw/workspace
    python3 test_integrated_day2.py
else
    echo -e "\n可用命令:"
    echo "  ./run_system_env.sh multifactor    # 测试多因子回归"
    echo "  ./run_system_env.sh alpha          # 测试Alpha预测器"
    echo "  ./run_system_env.sh walkforward    # 测试Walk-forward回测"
    echo "  ./run_system_env.sh factors        # 测试真实因子管理器"
    echo "  ./run_system_env.sh integrated     # 运行集成测试"
    echo ""
    echo "示例: ./run_system_env.sh multifactor"
fi