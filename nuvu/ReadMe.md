External Trigger Exposure Mode with ROI Data Extraction
=====================================================

This example demonstrates how to use the Nuvu Camera SDK to acquire images using external trigger signals in exposure mode, with automatic ROI (Region of Interest) setup and intensity data extraction. In this mode, the exposure time is controlled by the external TTL signal duration, and pixel intensity data from the ROI is extracted into 2D arrays.

Key Features:
- External trigger acquisition with exposure time controlled by TTL signal
- Support for both rising edge (LOW_HIGH) and falling edge (HIGH_LOW) trigger detection
- Automatic ROI configuration and validation
- High-speed intensity data extraction to 2D arrays
- Configurable number of images to acquire
- Automatic image saving in FITS format
- ROI data saved as text files for analysis
- Comprehensive error handling and memory management

How It Works:
1. The camera is configured to use external exposure trigger mode (EXT_LOW_HIGH_EXP or EXT_HIGH_LOW_EXP)
2. ROI is automatically configured and validated against camera capabilities
3. Each external TTL pulse triggers a new exposure
4. The exposure time equals the HIGH duration of the TTL signal
5. Images are acquired one per trigger pulse
6. ROI intensity data is extracted to pre-allocated 2D arrays: [x][y] where x=column, y=row
7. All images and ROI data are automatically saved

ROI Data Structure:
- 2D array format: intensityData[x][y]
- x represents column position (horizontal)
- y represents row position (vertical)
- Data type: 32-bit integers (compatible with 16-bit and 32-bit pixel formats)
- Automatic memory allocation and cleanup

Trigger Signal Requirements:
- Square wave TTL signal (0V to 5V or similar logic levels)
- Rising edge (LOW to HIGH) or falling edge (HIGH to LOW) detection
- HIGH duration determines exposure time
- LOW duration should be sufficient for readout completion
- Optimized for high-speed operation (few hundred nanoseconds pulse width)

High-Speed Optimization Features:
- Pre-allocated memory for ROI data arrays
- Minimal memory allocations during acquisition
- Direct data copying from image buffers
- Optimized timeout settings for high-speed triggers
- Efficient ROI data extraction algorithms
- Batch file I/O operations

Configuration:
- Modify g_FallingEdge variable to change trigger edge detection:
  * 0 = Rising edge (LOW_HIGH) - default
  * 1 = Falling edge (HIGH_LOW)
- Adjust ROI parameters in main():
  * roiData.x, roiData.y: ROI position
  * roiData.width, roiData.height: ROI dimensions
- Adjust nbrImages in main() to change the number of images to acquire
- Modify extTriggerTimeoutBuffer for different timeout settings

Usage:
1. Connect external TTL signal to camera trigger input
2. Adjust ROI parameters in the code if needed
3. Run the program
4. Generate TTL pulses to trigger image acquisition
5. Images are automatically saved as "ExtTriggerExp_Image_X.fits"
6. ROI data is saved as "ROI_Data_Image_X.txt"

Output Files:
- FITS images: "ExtTriggerExp_Image_X.fits"
- ROI data: "ROI_Data_Image_X.txt" (format: X Y Intensity)
- Each ROI data file contains header information and pixel-by-pixel intensity values

Performance Notes:
- Optimized for high-speed external triggers (few hundred nanoseconds)
- ROI data extraction adds minimal overhead to acquisition
- Memory usage: ROI size × 4 bytes per pixel (32-bit integers)
- File I/O is performed after each image to ensure data persistence

Notes:
- This mode requires camera support for external exposure trigger modes
- Shutter mode AUTO is not compatible with this trigger mode
- ROI functionality requires camera support for multiple ROI
- Ensure TTL signal timing allows for complete readout between pulses
- Check camera documentation for supported trigger signal specifications
- ROI dimensions are automatically validated and adjusted if out of bounds
