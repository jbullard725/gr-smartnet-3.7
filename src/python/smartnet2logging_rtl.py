#!/usr/bin/env python
""" 
	This program decodes the Motorola SmartNet II trunking protocol from the control channel
	Tune it to the control channel center freq, and it'll spit out the decoded packets.
	In what format? Who knows.

	This program does not include audio output support. It logs channels to disk by talkgroup name. If you don't specify what talkgroups to log, it logs EVERYTHING.
"""

from gnuradio import gr, gru, blks2, optfir, digital
from grc_gnuradio import blks2 as grc_blks2
from gnuradio import audio
from gnuradio import eng_notation
from fsk_demod import fsk_demod
from logging_receiver_dsd import logging_receiver
from optparse import OptionParser
from gnuradio.eng_option import eng_option
from gnuradio import smartnet

import osmosdr
from gnuradio import digital

#from pkt import *
import time
import gnuradio.gr.gr_threading as _threading
import csv
import os

class top_block_runner(_threading.Thread):
    def __init__(self, tb):
        _threading.Thread.__init__(self)
        self.setDaemon(1)
        self.tb = tb
        self.done = False
        self.start()
        
    def run(self):
        self.tb.run()
        self.done = True

class my_top_block(gr.top_block):
	def __init__(self, options, queue):
		gr.top_block.__init__(self)
                
                
		self.rtl = osmosdr.source_c( args="nchan=" + str(1) + " " + ""  )
		self.rtl.set_sample_rate(options.rate)
		self.rtl.set_center_freq(options.centerfreq, 0)
		self.rtl.set_freq_corr(options.ppm, 0)
		self.rtl.set_gain_mode(1, 0)
		
		self.centerfreq = options.centerfreq
		print "Tuning to: %fMHz" % (self.centerfreq - options.error)
		if not(self.tune(options.centerfreq - options.error)):
			print "Failed to set initial frequency"

		if options.gain is None: 
			options.gain = 10
		if options.bbgain is None: 
			options.bbgain = 25
		if options.ifgain is None: 
			options.ifgain = 25


		print "Setting RF gain to %i" % options.gain
		print "Setting BB gain to %i" % options.bbgain
		print "Setting IF gain to %i" % options.ifgain

		self.rtl.set_gain(options.gain, 0) 
		self.rtl.set_if_gain(options.ifgain,0)
		self.rtl.set_bb_gain(options.bbgain,0)
		
                self.rate = options.rate
		
		print "Samples per second is %i" % self.rate

		self._syms_per_sec = 3600;

		options.audiorate = 11025
		options.rate = self.rate

		options.samples_per_second = self.rate #yeah i know it's on the list
		options.syms_per_sec = self._syms_per_sec
		options.gain_mu = 0.01
		options.mu=0.5
		options.omega_relative_limit = 0.3
		options.syms_per_sec = self._syms_per_sec
		options.offset = options.centerfreq - options.freq
		print "Control channel offset: %f" % options.offset


                self.offset = gr.sig_source_c(self.rate, gr.GR_SIN_WAVE,
                                              options.offset, 1.0, 0.0)
		
		# for some reason using the xlating filter to do the offset makes thing barf with the HackRF, Multiply CC seems to work

		options.offset = 0

                self.mixer = gr.multiply_cc()

		self.demod = fsk_demod(options)

		self.start_correlator = gr.correlate_access_code_tag_bb("10101100",
                                                            0,
                                                            "smartnet_preamble") #should mark start of packet



		self.smartnet_deinterleave = smartnet.deinterleave()
		self.smartnet_crc = smartnet.crc(queue)

		
                self.connect(self.rtl, (self.mixer, 0))
                self.connect(self.offset, (self.mixer, 1))                    
                self.connect(self.mixer, self.demod)

		self.connect(self.demod, self.start_correlator, self.smartnet_deinterleave, self.smartnet_crc)
		
	def tune(self, freq):
		result = self.rtl.set_center_freq(freq)
		return True

def getfreq(chanlist, cmd):
	if chanlist is None: #if no chanlist file, make a guess. there are four extant bandplan schemes, and i believe this one is the most common.
#		if cmd < 0x2d0:		
# Changed for rebanding
		if cmd < 0x1b8:	
			freq = float(cmd * 0.025 + 851.0125)
		elif cmd < 0x230:
			freq = float(cmd * 0.025 + 851.0125 - 10.9875)
		else:
			freq = None
	else: #program your channel listings, get the right freqs.
		if chanlist.get(str(cmd), None) is not None:
			freq = float(chanlist[str(cmd)])
		else:
			freq = None

	return freq

def parsefreq(s, chanlist):
	retfreq = None
	[address, groupflag, command] = s.split(",")
	command = int(command)
	address = int(address) & 0xFFF0
	groupflag = bool(groupflag)

	print "Command: %s " % (hex(command))
	if chanlist is None:
            
            #if command == 0x30B and groupflag is True and lastmsg.get("command", None) == 0x308 and address & 0x2000 and address & 0x0800:
            if command < 0x2d0  and lastmsg.get("command", None) == 0x308:
                retfreq = getfreq(chanlist, command)
	else:
		if chanlist.get(str(command), None) is not None: #if it falls into the channel somewhere
			retfreq = getfreq(chanlist, command)
        
        lastlastmsg = lastmsg
        lastmsg["command"]=command
        lastmsg["address"]=address


	return [retfreq, address] # mask so the squelch opens up on the entire group



def main():
	# Create Options Parser:
	parser = OptionParser (option_class=eng_option, conflict_handler="resolve")
	expert_grp = parser.add_option_group("Expert")

	parser.add_option("-f", "--freq", type="eng_float", default=866.9625e6,
						help="set control channel frequency to MHz [default=%default]", metavar="FREQ")
	parser.add_option("-c", "--centerfreq", type="eng_float", default=867.5e6,
						help="set center receive frequency to MHz [default=%default]. Set to center of 800MHz band for best results")
	parser.add_option("-g", "--gain", type="int", default=None,
						help="set RF gain", metavar="dB")
	parser.add_option("-i", "--ifgain", type="int", default=None,
						help="set IF gain", metavar="dB")
	parser.add_option("-b", "--bbgain", type="int", default=None,
						help="set BB gain", metavar="dB")
	parser.add_option("-r", "--rate", type="eng_float", default=64e6/18,
						help="set sample rate [default=%default]")
#	parser.add_option("-b", "--bandwidth", type="eng_float", default=3e6,
#						help="set bandwidth of DBS RX frond end [default=%default]")
	parser.add_option("-C", "--chanlistfile", type="string", default=None,
						help="read in list of Motorola channel frequencies (improves accuracy of frequency decoding) [default=%default]")
	parser.add_option("-E", "--error", type="eng_float", default=5.5,
						help="enter an offset error to compensate for USRP clock inaccuracy")
	parser.add_option("-m", "--monitor", type="int", default=None,
						help="monitor a specific talkgroup")
	parser.add_option("-v", "--volume", type="eng_float", default=3.0,
						help="set volume gain for audio output [default=%default]")
	parser.add_option("-s", "--squelch", type="eng_float", default=28,
						help="set audio squelch level (default=%default, play with it)")
	parser.add_option("-D", "--directory", type="string", default="./log",
						help="choose a directory in which to save log data [default=%default]")
#	parser.add_option("-a", "--addr", type="string", default="",
#						help="address options to pass to UHD")
#	parser.add_option("-s", "--subdev", type="string",
#						help="UHD subdev spec", default=None)
#	parser.add_option("-A", "--antenna", type="string", default=None,
#					help="select Rx Antenna where appropriate")
# FOR RTL
	parser.add_option("-p", "--ppm", type="eng_float", default=0,
						help="set RTL PPM frequency adjustment [default=%default]")

	#receive_path.add_options(parser, expert_grp)

	(options, args) = parser.parse_args ()

	if len(args) != 0:
		parser.print_help(sys.stderr)
		sys.exit(1)


	if options.chanlistfile is not None:
		clreader=csv.DictReader(open(options.chanlistfile), quotechar='"')
		chanlist={"0": 0}
		for record in clreader:
			chanlist[record['channel']] = record['frequency']
	else:
		chanlist = None

	# build the graph
	queue = gr.msg_queue()
	tb = my_top_block(options, queue)

	runner = top_block_runner(tb)

	updaterate = 10 #main loop rate in Hz
	audiologgers = [] #this is the list of active audio sinks.
	rxfound = False #a flag to indicate whether or not an audio sink was found with the correct talkgroup ID; see below

	global dupes
	dupes = {0: 0}
	global lastmsg
	lastmsg = {"command": 0x0000, "address": 0x0000}
	global lastlastmsg
	lastlastmsg = lastmsg


	try:
		while 1:
			if not queue.empty_p():
				msg = queue.delete_head() # Blocking read
				sentence = msg.to_string()


	
				[newfreq, newaddr] = parsefreq(sentence, chanlist)

				monaddr = newaddr & 0xFFF0 #last 8 bits are status flags for a given talkgroup

				if newfreq is not None and int(newfreq*1e6) == int(options.freq):
					newfreq = None #don't log the audio from the trunk itself

				#we have a new frequency assignment. look through the list of audio logger objects and find if any of them have been allocated to it
				rxfound = False

				for rx in audiologgers:
                                    if newfreq is not None and abs(newfreq*1e6 - options.centerfreq) < options.rate/2:
                                        rx.tuneoffset(newfreq, options.centerfreq)
                                        rxfound = True
                                    
					print "Logger info: %i @ %f idle for %fs" % (rx.talkgroup, rx.getfreq(options.centerfreq), rx.timeout()) #TODO: debug

					#first look through the list to find out if there is a receiver assigned to this talkgroup
					if rx.talkgroup == monaddr: #here we've got one
						if newfreq != rx.getfreq(options.centerfreq) and newfreq is not None: #we're on a new channel, though
							rx.tuneoffset(newfreq, options.centerfreq)
						
						rx.unmute() #this should be unnecessary but it does update the timestamp
						rxfound = True
						print "New transmission on TG %i, updating timestamp" % rx.talkgroup

					else:
						if rx.getfreq(options.centerfreq) == newfreq: #a different talkgroup, but a new assignment on that freq! time to mute.
							rx.mute()

				if rxfound is False and newfreq is not None and abs(newfreq*1e6 - options.centerfreq) < options.rate/2: #no existing receiver for this talkgroup. time to create one.
					#lock the flowgraph
					tb.lock()
					print "Creating Logging Rec"
					audiologgers.append( logging_receiver(newaddr, options) ) #create it
					print "Tuning log recv"
					audiologgers[-1].tuneoffset(newfreq, options.centerfreq) #tune it
					print "Connecting log recv"
					tb.connect(tb.rtl, audiologgers[-1]) #connect to the flowgraph
					tb.unlock()
					print "Unmuting"
					audiologgers[-1].unmute() #unmute it

				if newfreq is not None:
					print "TG %i @ %f, %i active loggers" % (newaddr, newfreq, len(audiologgers))




			else:
				time.sleep(1.0/updaterate)

			for rx in audiologgers:
				if rx.timeout() >= 5.0: #if this receiver has been muted more than 3 seconds
					rx.close() #close the .wav file that the logger has been writing to
					tb.lock()
					tb.disconnect(rx)
					tb.unlock()
					audiologgers.remove(rx) #delete the audio logger object from the list

	except KeyboardInterrupt:
		#perform cleanup: time to get out of Dodge
		for rx in audiologgers: #you probably don't need to lock, disconnect, unlock, remove. but you might as well.
			rx.close()
			tb.lock()
			tb.disconnect(rx)
			tb.unlock()
			audiologgers.remove(rx)

		tb.stop()

		runner = None

if __name__ == '__main__':
	main()


