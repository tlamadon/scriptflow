# Julia script for estimating OLS coefficient

# Get arguments
counter = ARGS[1]

# Load additional dependencies
using DataFrames, CSV
using Distributions

# Simulate from a linear DGP 
nobs = 100
nX = 3
X = randn((nobs, nX))
β = ones(nX, 1)
ϵ = randn(nobs)
y = X * β + ϵ

# Calculates the ols coefficient
β_ols = inv(X' * X) * (X' * y)

# Compile the results to a data frame, only store the first coefficent
res = DataFrame(coef = β_ols[1], 
    absolute_bias = abs(β_ols[1] - β[1]),
    coef_0 = β[1])

# Construct directory and file names, then write to csv
dir_name = pwd() * "/temp"
file_name = "/res_" * counter * ".csv"
CSV.write(dir_name * file_name, res)