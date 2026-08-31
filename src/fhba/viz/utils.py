import numpy as np

def linear_scaling(ar,clip_min=None,clip_max=None,out_min=0,out_max=255):
    """
    Scale an input xarray.DataArray linearly to a specified output range, with optional clipping.
    
    Parameters
    ---------- 
    ar : xr.DataArray
        Input array to be scaled
    clip_min, clip_max : float, optional
        Minimum and maximum values to clip the input array before scaling. If None, the array's min and max are used.
    out_min, out_max : float, optional
        Minimum and maximum values of the output scaled array. Defaults to 0 and 255.

    Returns
    -------
    scaled : xr.DataArray
        Linearly scaled (and optionally clipped) array, with values in the range [out_min, out_max].
    """

    if clip_min is None:
        clip_min = float(ar.min())
    if clip_max is None:
        clip_max = float(ar.max())

    clipped = np.clip(ar,clip_min,clip_max)

    scaled = out_min + (clipped - clip_min) * (out_max - out_min) / (clip_max - clip_min)

    return scaled
    
def nonlinear_enhancement(
    arr, 
    input_breakpoints=[0,30,60,120,190,255],
    output_values=[0,110,160,210,240,255]
):
    
    """
    Apply a non-linear enhancement to the input array using arbitrary breakpoints.

    Parameters:
    arr (numpy.ndarray): The input array with values in the range [0, 0.8].
    input_breakpoints (list): A list of breakpoints in the input range.
    output_values (list): A list of corresponding output values for the breakpoints.

    Returns:
    numpy.ndarray: The enhanced array with values in the range defined by output_values.
    """
    if len(input_breakpoints) != len(output_values):
        raise ValueError("The number of input breakpoints must match the number of output values.")

    # Initialize an output array
    enhanced_arr = np.zeros_like(arr)

    # Apply piecewise linear transformations
    for i in range(len(input_breakpoints) - 1):
        # Define the current segment
        x0, x1 = input_breakpoints[i], input_breakpoints[i + 1]
        y0, y1 = output_values[i], output_values[i + 1]
        
        # Create a mask for the current segment
        mask = (arr >= x0) & (arr < x1)
        
        # Apply linear transformation for the current segment
        enhanced_arr[mask] = y0 + (arr[mask] - x0) * ((y1 - y0) / (x1 - x0))

    # Handle the last breakpoint
    mask_last = (arr == input_breakpoints[-1])
    enhanced_arr[mask_last] = output_values[-1]

    return enhanced_arr