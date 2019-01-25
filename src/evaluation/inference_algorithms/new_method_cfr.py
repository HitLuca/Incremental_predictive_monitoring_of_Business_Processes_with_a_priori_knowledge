"""
This file is build based on the code by Anton Yeshchenko found in evaluate_suffix_only.py

here the beam search algorithm is implemented with a modified queueing algorithm.

Author: Kaur Jarvpold
"""
from __future__ import division

import csv
import os.path
import time
from Queue import PriorityQueue
from datetime import datetime, timedelta
from itertools import izip

import distance
from keras.models import load_model
from sklearn import metrics

import shared_variables
from shared_variables import get_int_from_unicode
from evaluation.prepare_data_resource import amplify, select_declare_verified_traces, \
    encode, prepare_testing_data, create_queue, adjust_probabilities


def run_experiments(server_replayer, log_name, models_folder, fold):
    class NodePrediction:
        def __init__(self, data, trace_id, crop_line, crop_line_group, crop_times,
                     tot_predicted_time, probability_of=0):
            self.data = data
            self.trace_id = trace_id
            self.cropped_line = crop_line
            self.cropped_line_group = crop_line_group
            self.cropped_times = crop_times
            self.total_predicted_time = tot_predicted_time
            self.probability_of = probability_of

    model_filename = shared_variables.extract_last_model_checkpoint(log_name, models_folder, fold, 'CFR')
    declare_model_filename = shared_variables.extract_declare_model_filename(log_name)

    log_settings_dictionary = shared_variables.log_settings[log_name]
    prefix_size_pred_from = log_settings_dictionary['prefix_size_pred_from']
    prefix_size_pred_to = log_settings_dictionary['prefix_size_pred_to']

    start_time = time.time()

    beam_size = 10

    # prepare the data N.B. maxlen == predict_size
    lines, \
    lines_id, \
    lines_group, \
    lines_t, \
    lines_t2, \
    lines_t3, \
    lines_t4, \
    maxlen, \
    chars, \
    chars_group, \
    char_indices, \
    char_indices_group, \
    divisor, \
    divisor2, \
    divisor3, \
    predict_size, \
    target_indices_char, \
    target_indices_char_group, \
    target_char_indices, \
    target_char_indices_group = prepare_testing_data(log_name)

    # find cycles and modify the probability functionality goes here
    stop_symbol_probability_amplifier_current = 1

    # load model, set this to the model generated by train.py
    model = load_model(model_filename)

    folder_path = shared_variables.outputs_folder + models_folder + '/' + str(fold) + '/results/declare/'
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    output_filename = folder_path + '%s_%s.csv' % (log_name, 'CFR')

    with open(output_filename, 'wb') as csvfile:
        csv_writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        # headers for the new file
        csv_writer.writerow(["Prefix length",
                             "Ground truth",
                             "Predicted",
                             "Damerau-Levenshtein",
                             "Jaccard",
                             "Ground truth times",
                             "Predicted times",
                             "RMSE",
                             "MAE",
                             "Median AE",
                             "Ground Truth Resource",
                             "Predicted Resource",
                             "Damerau-Levenshtein Resource"])

        # make predictions for different prefix sizes as specified in 'shared variables'
        for prefix_size in range(prefix_size_pred_from, prefix_size_pred_to):
            print("prefix size: " + str(prefix_size))
            curr_time = time.time()

            lines_s, \
            lines_id_s, \
            lines_group_s, \
            lines_t_s, \
            lines_t2_s, \
            lines_t3_s, \
            lines_t4_s = select_declare_verified_traces(server_replayer,
                                                        declare_model_filename,
                                                        lines,
                                                        lines_id,
                                                        lines_group,
                                                        lines_t,
                                                        lines_t2,
                                                        lines_t3,
                                                        lines_t4,
                                                        prefix_size)

            print("formulas verified: " + str(len(lines_s)) + " out of : " + str(len(lines)))
            print('elapsed_time:', time.time() - curr_time)

            counter = 0
            for line, line_id, line_group, times, times2, times3, times4 in izip(lines_s,
                                                                                 lines_id_s,
                                                                                 lines_group_s,
                                                                                 lines_t_s,
                                                                                 lines_t2_s,
                                                                                 lines_t3_s,
                                                                                 lines_t4_s):
                times.append(0)
                cropped_line_id = line_id
                cropped_line = ''.join(line[:prefix_size])
                cropped_line_group = ''.join(line_group[:prefix_size])
                cropped_times = times[:prefix_size]
                cropped_times3 = times3[:prefix_size]
                cropped_times4 = times4[:prefix_size]

                if len(times2) < prefix_size:
                    continue  # make no prediction for this case, since this case has ended already

                # initialize root of the tree for beam search
                total_predicted_time_initialization = 0
                search_node_root = NodePrediction(encode(cropped_line, cropped_line_group, cropped_times,
                                                         cropped_times3, maxlen, chars, chars_group,
                                                         char_indices, char_indices_group, divisor, divisor2),
                                                  cropped_line_id,
                                                  cropped_line,
                                                  cropped_line_group,
                                                  cropped_times4,
                                                  total_predicted_time_initialization)

                ground_truth = ''.join(line[prefix_size:prefix_size + predict_size])
                ground_truth_group = ''.join(line_group[prefix_size:prefix_size + predict_size])
                ground_truth_t = times2[prefix_size - 1]
                case_end_time = times2[len(times2) - 1]
                ground_truth_t = case_end_time - ground_truth_t

                queue_next_steps = PriorityQueue()
                queue_next_steps.put((-search_node_root.probability_of, search_node_root))

                queue_next_steps_future = PriorityQueue()
                start_of_the_cycle_symbol = " "
                found_satisfying_constraint = False

                current_beam_size = beam_size
                current_prediction_premis = None

                for i in range(predict_size):
                    for k in range(current_beam_size):
                        if queue_next_steps.empty():
                            break

                        _, current_prediction_premis = queue_next_steps.get()

                        if not found_satisfying_constraint:
                            if server_replayer.verify_with_data(declare_model_filename,
                                                                current_prediction_premis.trace_id,
                                                                current_prediction_premis.cropped_line,
                                                                current_prediction_premis.cropped_line_group,
                                                                current_prediction_premis.cropped_times,
                                                                prefix_size):
                                # the formula verified and we can just finish the predictions
                                # beam size is 1 because predict only sequence of events
                                activity = map(lambda x: str(get_int_from_unicode(x)),
                                               current_prediction_premis.cropped_line[prefix_size:])
                                group = map(lambda x: str(get_int_from_unicode(x)),
                                            current_prediction_premis.cropped_line_group[prefix_size:])

                                print 'changed beam size  ' + str(activity) + '  ' + str(group)
                                current_prediction_premis.probability_of = 0.0
                                current_beam_size = 1
                                # overwrite new queue
                                queue_next_steps_future = PriorityQueue()
                                found_satisfying_constraint = True

                        enc = current_prediction_premis.data
                        current_trace_id = current_prediction_premis.trace_id
                        temp_cropped_line = current_prediction_premis.cropped_line
                        temp_cropped_line_group = current_prediction_premis.cropped_line_group
                        temp_cropped_times = current_prediction_premis.cropped_times
                        y = model.predict(enc, verbose=0)  # make predictions
                        # split predictions into separate activity and time predictions
                        y_char = y[0][0]
                        y_group = y[1][0]
                        y_t = y[2][0][0]

                        if not i == 0:
                            stop_symbol_probability_amplifier_current, start_of_the_cycle_symbol = \
                                amplify(temp_cropped_line)

                        y_char_adjusted = adjust_probabilities(y_char, target_char_indices, start_of_the_cycle_symbol,
                                                               stop_symbol_probability_amplifier_current)

                        the_queue = create_queue(y_char_adjusted, y_group)

                        if y_t < 0:
                            y_t = 0
                        cropped_times.append(y_t)

                        y_t = y_t * divisor3
                        cropped_times3.append(cropped_times3[-1] + timedelta(seconds=y_t))

                        for j in range(current_beam_size):
                            probability_this, indexes = the_queue.get()

                            temp_prediction = target_indices_char[indexes[0]]
                            temp_prediction_group = target_indices_char_group[indexes[1]]

                            # end of case was just predicted, therefore, stop predicting further into the future
                            if temp_prediction == '!':
                                if server_replayer.verify_with_data(declare_model_filename,
                                                    current_trace_id,
                                                    temp_cropped_line,
                                                    temp_cropped_line_group,
                                                    temp_cropped_times,
                                                    prefix_size):
                                    stop_symbol_probability_amplifier_current = 1
                                    # print '! predicted, end case'
                                    queue_next_steps = PriorityQueue()
                                    break
                                else:
                                    continue

                            temp_cropped_line = current_prediction_premis.cropped_line + temp_prediction
                            temp_cropped_line_group = \
                                current_prediction_premis.cropped_line_group + temp_prediction_group

                            # adds a fake timestamp to the list
                            t = time.strptime(cropped_times4[-1], "%Y-%m-%d %H:%M:%S")
                            new_timestamp = datetime.fromtimestamp(time.mktime(t)) + timedelta(0, 2000)
                            cropped_times4.append(new_timestamp.strftime("%Y-%m-%d %H:%M:%S"))

                            temp_total_predicted_time = current_prediction_premis.total_predicted_time + y_t
                            temp_state_data = encode(temp_cropped_line,
                                                     temp_cropped_line_group,
                                                     cropped_times,
                                                     cropped_times3,
                                                     maxlen,
                                                     chars,
                                                     chars_group,
                                                     char_indices,
                                                     char_indices_group,
                                                     divisor,
                                                     divisor2)

                            temp = NodePrediction(temp_state_data,
                                                  cropped_line_id,
                                                  temp_cropped_line,
                                                  temp_cropped_line_group,
                                                  cropped_times4,
                                                  temp_total_predicted_time,
                                                  current_prediction_premis.probability_of + probability_this)

                            queue_next_steps_future.put((temp.probability_of, temp))
                            # print 'INFORMATION: ' + str(counter) + ' ' + str(i) + ' ' + str(k) + ' ' + str(j) + ' ' + \
                            #       temp_cropped_line[prefix_size:] + "     " + ground_truth + "     " + \
                            #       temp_cropped_line_group[prefix_size:] + "     " + ground_truth_group + "     " + \
                            #       str(temp.probability_of) + '   ' + str(current_beam_size)

                    queue_next_steps = queue_next_steps_future
                    queue_next_steps_future = PriorityQueue()

                counter += 1

                if current_prediction_premis is None:
                    print "Cannot find any trace that is compliant with formula given current beam size"
                    break

                output = []

                if current_prediction_premis is None:
                    predicted = u""
                    predicted_group = u""
                    total_predicted_time = 0
                else:
                    predicted = (current_prediction_premis.cropped_line[prefix_size:])
                    predicted_group = (current_prediction_premis.cropped_line_group[prefix_size:])
                    total_predicted_time = current_prediction_premis.total_predicted_time

                if len(ground_truth) > 0:
                    output.append(prefix_size)
                    output.append(unicode(ground_truth).encode("utf-8"))
                    output.append(unicode(predicted).encode("utf-8"))
                    output.append(1 - distance.nlevenshtein(predicted, ground_truth))
                    output.append(1 - distance.jaccard(predicted, ground_truth))
                    output.append(ground_truth_t)
                    output.append(total_predicted_time)
                    output.append('')
                    output.append(metrics.mean_absolute_error([ground_truth_t], [total_predicted_time]))
                    output.append(metrics.median_absolute_error([ground_truth_t], [total_predicted_time]))
                    output.append(unicode(ground_truth_group).encode("utf-8"))
                    output.append(unicode(predicted_group).encode("utf-8"))
                    output.append(1 - distance.nlevenshtein(predicted_group, ground_truth_group))
                    csv_writer.writerow(output)

    print("TIME TO FINISH --- %s seconds ---" % (time.time() - start_time))
