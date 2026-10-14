/********************* this version Driver is created on the first day of 2009 chinese new year, Bull's year *******************/

this version driver support both USB20IR and USB20ACCD
USB20IR   : SU512/SU256
USB20ACCD : S10420-1006/S10420-1106



本驱动支持的硬件：
1. USB20IR    NC-V4 (MCU程序在PC端)
	SU256LSB
	SU512LDB
	SU256LDB

2. USB20ACCD-V1     (MCU程序在电路板上)
	S10420-1006
	S10420-1106	

3. USB20ACCD-V2     (MCU程序在电路板上)
	S10420-1006
	S10420-1106

/////////////////////////////////////////////////////////////////////////////
驱动信息
20080501 release
在USB20IR NC-V4中增加了外触发支持；在曝光时，电路板上的灯关闭。其他内容未变

/////////////////////////////////////////////////////////////////////////////
驱动信息
20080818 release

(1). 增加了FPGA 0x80 ~ 0xFF操作指令（读写）
(2). 增加了对OPSW 4x1的支持