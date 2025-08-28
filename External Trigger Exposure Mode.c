//This is a header file that needs to be added to all your projects as all the nc functions are defined here
#include "nc_driver.h"

//This header defines the initialize and cleanUp functions
#include "../Utility/utility.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

// Structure to hold ROI information and intensity data
typedef struct {
    int x, y;           // ROI position
    int width, height;  // ROI dimensions
    int** intensityData; // 2D array: [x][y] where x=column, y=row
} ROIData;

int externalTriggerExposureMode(NcCam camera, const int nbrImages, ROIData* roiData);
int testExtExpTriggerMode(NcCam camera, const int imageNumber, ROIData* roiData);
int setupROI(NcCam camera, ROIData* roiData);
int extractROIIntensityData(NcImage* image, int imageWidth, int imageHeight, ROIData* roiData, int pixelBits);
void allocateROIMemory(ROIData* roiData);
void freeROIMemory(ROIData* roiData);
void saveROIDataToFile(ROIData* roiData, const int imageNumber);

//Global variable to control trigger edge detection
//Set to 1 to use a trigger pulse with a falling leading edge (HIGH_LOW)
//Set to 0 to use a trigger pulse with a rising leading edge (LOW_HIGH)
int g_FallingEdge = 0;

int main()
{
	NcCam myCam = NULL;
	int error = initialize(&myCam);
	
	if (error == NC_SUCCESS) {
		if (g_FallingEdge == 0) {
			printf("\n*** External Trigger Exposure Mode with ROI Data Extraction ***\n");
			printf("*** Please supply square trigger pulses with a RISING leading edge.\n");
			printf("*** Each trigger pulse will start an exposure.\n");
			printf("*** The exposure time will be equal to the HIGH duration of the TTL signal.\n");
			printf("*** ROI intensity data will be extracted and saved as arrays.\n\n");
		} else {
			printf("\n*** External Trigger Exposure Mode with ROI Data Extraction ***\n");
			printf("*** Please supply square trigger pulses with a FALLING leading edge.\n");
			printf("*** Each trigger pulse will start an exposure.\n");
			printf("*** The exposure time will be equal to the HIGH duration of the TTL signal.\n");
			printf("*** ROI intensity data will be extracted and saved as arrays.\n\n");
		}

		const int nbrImages = 5; // Number of images to acquire
		
		// Initialize ROI data structure
		ROIData roiData = {0};
		roiData.x = 100;        // ROI X position (adjust as needed)
		roiData.y = 100;        // ROI Y position (adjust as needed)
		roiData.width = 64;     // ROI width (adjust as needed)
		roiData.height = 64;    // ROI height (adjust as needed)
		
		// Allocate memory for ROI intensity data
		allocateROIMemory(&roiData);
		
		error = externalTriggerExposureMode(myCam, nbrImages, &roiData);
		
		// Free allocated memory
		freeROIMemory(&roiData);
	}
	
	if (error != NC_SUCCESS) {
		printf("The error %d happened during the example. For more information about this error, the file nc_error.h can be used\n", error);
	}

	cleanUp(myCam);
	
	return error;
}

int externalTriggerExposureMode(NcCam camera, const int nbrImages, ROIData* roiData)
{
	printf("Starting external trigger exposure mode acquisition with ROI data extraction...\n");
	printf("Will acquire %d images, one per trigger pulse.\n", nbrImages);
	printf("ROI: Position(%d,%d), Size(%dx%d)\n", roiData->x, roiData->y, roiData->width, roiData->height);
	
	// Check if the camera supports external exposure trigger modes
	int error = ncCamParamAvailable(camera, TRIGGER_MODE, EXT_LOW_HIGH_EXP);
	if (error == NC_ERROR_CAM_NO_FEATURE) {
		printf("This camera does not support external exposure trigger modes.\n");
		printf("Please check your camera model and firmware version.\n");
		return NC_ERROR_CAM_NO_FEATURE;
	} else if (error != NC_SUCCESS) {
		return error;
	}
	
	printf("External exposure trigger mode is supported.\n");
	
	// Setup ROI before starting acquisition
	error = setupROI(camera, roiData);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Open the shutter for the acquisition
	// NOTE: shutter mode AUTO is not compatible with external exposure trigger modes
	error = ncCamSetShutterMode(camera, OPEN);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Get the readout time for timeout calculation
	double readoutTime;
	error = ncCamGetReadoutTime(camera, &readoutTime);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// For high-speed acquisition, use a shorter timeout
	// The timeout should be optimized for your specific TTL timing
	const int extTriggerTimeoutBuffer = 1000; // 1 second buffer for high-speed operation
	error = ncCamSetTimeout(camera, (int)readoutTime + extTriggerTimeoutBuffer);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Choose the appropriate trigger mode based on the desired edge detection
	enum TriggerMode trigExternal;
	if (g_FallingEdge == 1) {
		trigExternal = EXT_HIGH_LOW_EXP; // Falling edge (HIGH to LOW transition)
		printf("Using EXT_HIGH_LOW_EXP trigger mode (falling edge).\n");
	} else {
		trigExternal = EXT_LOW_HIGH_EXP; // Rising edge (LOW to HIGH transition)
		printf("Using EXT_LOW_HIGH_EXP trigger mode (rising edge).\n");
	}
	  
	// Set the trigger mode
	// The last parameter (nbrImages) is set to 0 because it is not taken into account in this mode
	error = ncCamSetTriggerMode(camera, trigExternal, 0);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	printf("Camera configured for external exposure trigger mode.\n");
	printf("Ready to receive %d trigger pulses...\n\n", nbrImages);
	
	// Acquire images one by one, each triggered by an external pulse
	for (int i = 0; i < nbrImages; ++i) {
		printf("Waiting for trigger pulse %d/%d...\n", i + 1, nbrImages);
		
		error = testExtExpTriggerMode(camera, i, roiData);
		if (error != NC_SUCCESS) {
			printf("Error acquiring image %d: %d\n", i + 1, error);
			return error;
		}
		
		printf("Image %d/%d acquired and ROI data extracted successfully.\n", i + 1, nbrImages);
	}
	
	printf("\nAll %d images acquired successfully!\n", nbrImages);
	
	// Close the shutter now that the acquisition is complete
	error = ncCamSetShutterMode(camera, CLOSE);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	return NC_SUCCESS;
}

int setupROI(NcCam camera, ROIData* roiData)
{
	printf("Setting up ROI...\n");
	
	// Check if multiple ROI is supported
	int roiCountMax = -1;
	int error = ncCamGetMRoiCountMax(camera, &roiCountMax);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	if (roiCountMax < 1) {
		printf("ROI functionality not supported by this camera.\n");
		return NC_ERROR_CAM_NO_FEATURE;
	}
	
	printf("Camera supports up to %d ROIs.\n", roiCountMax);
	
	// Get the maximum available image size
	int maxWidth, maxHeight;
	error = ncCamGetMaxSize(camera, &maxWidth, &maxHeight);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	printf("Maximum image size: %dx%d\n", maxWidth, maxHeight);
	
	// Validate ROI parameters
	if (roiData->x < 0 || roiData->y < 0 || 
		roiData->x + roiData->width > maxWidth || 
		roiData->y + roiData->height > maxHeight) {
		printf("ROI parameters out of bounds. Adjusting...\n");
		
		// Adjust ROI to fit within bounds
		if (roiData->x < 0) roiData->x = 0;
		if (roiData->y < 0) roiData->y = 0;
		if (roiData->x + roiData->width > maxWidth) {
			roiData->x = maxWidth - roiData->width;
			if (roiData->x < 0) {
				roiData->x = 0;
				roiData->width = maxWidth;
			}
		}
		if (roiData->y + roiData->height > maxHeight) {
			roiData->y = maxHeight - roiData->height;
			if (roiData->y < 0) {
				roiData->y = 0;
				roiData->height = maxHeight;
			}
		}
		
		printf("Adjusted ROI: Position(%d,%d), Size(%dx%d)\n", 
			   roiData->x, roiData->y, roiData->width, roiData->height);
	}
	
	// Set the ROI size and position
	error = ncCamSetMRoiSize(camera, 0, roiData->width, roiData->height);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	error = ncCamSetMRoiPosition(camera, 0, roiData->x, roiData->y);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Apply the ROI configuration
	error = ncCamMRoiApply(camera);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	printf("ROI configured successfully.\n");
	return NC_SUCCESS;
}

int testExtExpTriggerMode(NcCam camera, const int imageNumber, ROIData* roiData)
{
	int error;
	// Launch an acquisition by the framegrabber and request an image from the camera
	// This function does not wait for the acquisition to be complete before returning
	error = ncCamStart(camera, 1);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Read the image received
	// If a timeout occurs, an error code will be returned
	NcImage* myNcImage;
	error = ncCamRead(camera, &myNcImage);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Determine pixel depth from controller to interpret image data type
	int pixelBits = 0;
	error = ncCamGetControllerPixelDepth(camera, &pixelBits);
	if (error != NC_SUCCESS) {
		return error;
	}

	// Get expected image dimensions from camera context
	int imageWidth = 0, imageHeight = 0;
	error = ncCamGetSize(camera, &imageWidth, &imageHeight);
	if (error != NC_SUCCESS) {
		return error;
	}

	// Extract ROI intensity data to the pre-allocated array
	error = extractROIIntensityData(myNcImage, imageWidth, imageHeight, roiData, pixelBits);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	// Save ROI data to file
	saveROIDataToFile(roiData, imageNumber);
	
	// Create a descriptive filename for the image
	char imageName[64];
	sprintf(imageName, "ExtTriggerExp_Image_%d", imageNumber);
	
	// Save the image acquired
	// The exposure time of the image will be the length of the trigger pulse
	// "External exposure trigger mode image" parameter is used to add a comment header
	// Overwrite flag set to '1' to overwrite an existing file if it has the same name
	error = ncCamSaveImage(camera, myNcImage, imageName, FITS, "External exposure trigger mode image - exposure time controlled by TTL signal", 1);
	if (error != NC_SUCCESS) {
		return error;
	}
	
	printf("Saved image: %s and ROI data\n", imageName);
	
	return NC_SUCCESS;
}

int extractROIIntensityData(NcImage* image, int imageWidth, int imageHeight, ROIData* roiData, int pixelBits)
{
	// Validate ROI bounds
	if (roiData->x < 0 || roiData->y < 0 || 
		roiData->x + roiData->width > imageWidth || 
		roiData->y + roiData->height > imageHeight) {
		printf("Warning: ROI extends beyond image boundaries.\n");
		return NC_ERROR_CAM_PARAM_OUT;
	}
	
	// Extract ROI data based on pixel depth
	// Fast copy without additional processing
	if (pixelBits <= 16) {
		uint16_t* data = (uint16_t*)image;
		for (int x = 0; x < roiData->width; x++) {
			for (int y = 0; y < roiData->height; y++) {
				int imageX = roiData->x + x;
				int imageY = roiData->y + y;
				int imageIndex = imageY * imageWidth + imageX;
				roiData->intensityData[x][y] = (int)data[imageIndex];
			}
		}
	} else if (pixelBits <= 32) {
		uint32_t* data = (uint32_t*)image;
		for (int x = 0; x < roiData->width; x++) {
			for (int y = 0; y < roiData->height; y++) {
				int imageX = roiData->x + x;
				int imageY = roiData->y + y;
				int imageIndex = imageY * imageWidth + imageX;
				roiData->intensityData[x][y] = (int)data[imageIndex];
			}
		}
	} else {
		printf("Unsupported pixel depth: %d bits\n", pixelBits);
		return NC_ERROR_DATA_TYPE;
	}
	
	return NC_SUCCESS;
}

void allocateROIMemory(ROIData* roiData)
{
	// Allocate 2D array: [x][y] where x=column, y=row
	roiData->intensityData = (int**)malloc(roiData->width * sizeof(int*));
	if (roiData->intensityData == NULL) {
		printf("Error: Failed to allocate memory for ROI data.\n");
		return;
	}
	
	for (int x = 0; x < roiData->width; x++) {
		roiData->intensityData[x] = (int*)malloc(roiData->height * sizeof(int));
		if (roiData->intensityData[x] == NULL) {
			printf("Error: Failed to allocate memory for ROI data row %d.\n", x);
			// Clean up already allocated memory
			for (int i = 0; i < x; i++) {
				free(roiData->intensityData[i]);
			}
			free(roiData->intensityData);
			roiData->intensityData = NULL;
			return;
		}
	}
	
	printf("ROI memory allocated: %dx%d array\n", roiData->width, roiData->height);
}

void freeROIMemory(ROIData* roiData)
{
	if (roiData->intensityData != NULL) {
		for (int x = 0; x < roiData->width; x++) {
			if (roiData->intensityData[x] != NULL) {
				free(roiData->intensityData[x]);
			}
		}
		free(roiData->intensityData);
		roiData->intensityData = NULL;
	}
}

void saveROIDataToFile(ROIData* roiData, const int imageNumber)
{
	char filename[128];
	sprintf(filename, "ROI_Data_Image_%d.txt", imageNumber);
	
	FILE* file = fopen(filename, "w");
	if (file == NULL) {
		printf("Warning: Could not open file %s for writing.\n", filename);
		return;
	}
	
	// Write header information
	fprintf(file, "# ROI Intensity Data - Image %d\n", imageNumber);
	fprintf(file, "# Format: X Y Intensity\n");
	fprintf(file, "# ROI Position: (%d, %d)\n", roiData->x, roiData->y);
	fprintf(file, "# ROI Size: %dx%d\n", roiData->width, roiData->height);
	fprintf(file, "# Data format: [x][y] where x=column, y=row\n\n");
	
	// Write data in a format that can be easily imported into analysis software
	for (int y = 0; y < roiData->height; y++) {
		for (int x = 0; x < roiData->width; x++) {
			fprintf(file, "%d %d %d\n", x, y, roiData->intensityData[x][y]);
		}
	}
	
	fclose(file);
	printf("ROI data saved to: %s\n", filename);
}
