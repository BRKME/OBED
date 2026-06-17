"""
Минимальные ABI — только те функции/структуры, которые реально используются ботом.
Полные ABI Uniswap V3 не нужны и только увеличивают поверхность для ошибок.
"""

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

POOL_ABI = [
    {"name": "slot0", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"},
        {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"},
        {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"},
        {"name": "feeProtocol", "type": "uint8"},
        {"name": "unlocked", "type": "bool"},
     ]},
    {"name": "tickSpacing", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "int24"}]},
    {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"name": "token1", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"name": "fee", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint24"}]},
    {"name": "liquidity", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint128"}]},
]

FACTORY_ABI = [
    {"name": "getPool", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"},
                {"name": "fee", "type": "uint24"}],
     "outputs": [{"name": "pool", "type": "address"}]},
]

POSITION_MANAGER_ABI = [
    {"name": "mint", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "token0", "type": "address"},
         {"name": "token1", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "tickLower", "type": "int24"},
         {"name": "tickUpper", "type": "int24"},
         {"name": "amount0Desired", "type": "uint256"},
         {"name": "amount1Desired", "type": "uint256"},
         {"name": "amount0Min", "type": "uint256"},
         {"name": "amount1Min", "type": "uint256"},
         {"name": "recipient", "type": "address"},
         {"name": "deadline", "type": "uint256"},
     ]}],
     "outputs": [
        {"name": "tokenId", "type": "uint256"},
        {"name": "liquidity", "type": "uint128"},
        {"name": "amount0", "type": "uint256"},
        {"name": "amount1", "type": "uint256"},
     ]},
    {"name": "decreaseLiquidity", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenId", "type": "uint256"},
         {"name": "liquidity", "type": "uint128"},
         {"name": "amount0Min", "type": "uint256"},
         {"name": "amount1Min", "type": "uint256"},
         {"name": "deadline", "type": "uint256"},
     ]}],
     "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}]},
    {"name": "collect", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenId", "type": "uint256"},
         {"name": "recipient", "type": "address"},
         {"name": "amount0Max", "type": "uint128"},
         {"name": "amount1Max", "type": "uint128"},
     ]}],
     "outputs": [{"name": "amount0", "type": "uint256"}, {"name": "amount1", "type": "uint256"}]},
    {"name": "burn", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "tokenId", "type": "uint256"}], "outputs": []},
    {"name": "positions", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}],
     "outputs": [
        {"name": "nonce", "type": "uint96"},
        {"name": "operator", "type": "address"},
        {"name": "token0", "type": "address"},
        {"name": "token1", "type": "address"},
        {"name": "fee", "type": "uint24"},
        {"name": "tickLower", "type": "int24"},
        {"name": "tickUpper", "type": "int24"},
        {"name": "liquidity", "type": "uint128"},
        {"name": "feeGrowthInside0LastX128", "type": "uint256"},
        {"name": "feeGrowthInside1LastX128", "type": "uint256"},
        {"name": "tokensOwed0", "type": "uint128"},
        {"name": "tokensOwed1", "type": "uint128"},
     ]},
]

SWAP_ROUTER02_ABI = [
    {"name": "exactInputSingle", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "recipient", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMinimum", "type": "uint256"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"},
     ]}],
     "outputs": [{"name": "amountOut", "type": "uint256"}]},
]
