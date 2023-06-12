# Script for aggeragating simulations results

# Get arguments
dir_name = ARGS[1]

# Load dependencies
using DataFrames, CSV
using Statistics

# Construct the directory name and get the file names
file_names = readdir(dir_name)
n_files = length(file_names)

# Get dimensions from first file 
file_name = dir_name * "/" * file_names[1]
res_1 = CSV.read(file_name, DataFrame)

# Collect simulation results from each file
res_array = zeros((nrow(res_1), ncol(res_1), n_files))
for j in 1:n_files
    # Load file
    file_name = dir_name * "/" * file_names[j]
    res_j = CSV.read(file_name, DataFrame)
    # Store the results
    res_array[:, :, j] .= res_j
end

# Aggregation ==================================================================

# Calculate the mean, standard deviation, and median for each passed variable
#    is calculated. 
res_mat = cat(mapslices(mean, res_array, dims = 3)[1, :, 1],
    mapslices(std, res_array, dims = 3)[1, :, 1],
    mapslices(median, res_array, dims = 3)[1, :, 1], dims = 2)

# Results are stored in a data frame.
res_df = DataFrame(res_mat, :auto)
rename!(res_df, [:mean, :std, :median])
insertcols!(res_df, 1, :Variable => names(res_1))

# Add the number of simulations
push!(res_df, ["Number of simulations" n_files 0 0])

# Store aggregated simulation results ==========================================

# Construct file names
file_name = pwd() * "/results.csv"

# Write the data frame to a .csv file
CSV.write(file_name, res_df)