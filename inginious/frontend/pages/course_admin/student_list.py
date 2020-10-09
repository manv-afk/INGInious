# -*- coding: utf-8 -*-
#
# This file is part of INGInious. See the LICENSE and the COPYRIGHTS files for
# more information about the licensing of this file.
import json
from collections import OrderedDict

import web
import yaml
from bson import ObjectId
from pymongo import ReturnDocument

from inginious.common import custom_yaml
from inginious.frontend.pages.course_admin.utils import make_csv, INGIniousAdminPage


class CourseStudentListPage(INGIniousAdminPage):
    """ Course administration page: list of registered students """

    def GET_AUTH(self, courseid):  # pylint: disable=arguments-differ
        """ GET request """
        course, __ = self.get_course_and_check_rights(courseid)

        if "download_audiences" in web.input():
            web.header('Content-Type', 'text/x-yaml', unique=True)
            web.header('Content-Disposition', 'attachment; filename="audiences.yaml"', unique=True)
            audiences = [{"description": audience["description"],
                           "students": audience["students"],
                           "tutors": audience["tutors"]} for audience in
                          self.user_manager.get_course_audiences(course)]

            return yaml.dump(audiences)

        if "download_groups" in web.input():
            web.header('Content-Type', 'text/x-yaml', unique=True)
            web.header('Content-Disposition', 'attachment; filename="groups.yaml"', unique=True)
            groups = [{"description": group["description"],
                           "students": group["students"],
                           "size": group["size"],
                            "audiences": [str(c) for c in group["audiences"]]} for group in
                          self.user_manager.get_course_groups(course)]

            return yaml.dump(groups)

        return self.page(course, active_tab="tab_audiences" if "audiences" in web.input() else "tab_students")

    def POST_AUTH(self, courseid):  # pylint: disable=arguments-differ
        """ POST request """
        course, __ = self.get_course_and_check_rights(courseid, None, True)
        data = web.input(delete=[], groupfile={}, audiencefile={})
        error = {}
        msg = {}
        active_tab = "tab_students"

        self.post_student_list(course, data)
        active_tab = self.post_audiences(course, data, active_tab, msg, error)
        active_tab = self.post_groups(course, data, active_tab, msg, error)

        return self.page(course, active_tab, msg, error)

    def submission_url_generator_user(self, username):
        """ Generates a submission url """
        return "?format=taskid%2Fusername&users=" + username

    def submission_url_generator_audience(self, audienceid):
        """ Generates a submission url """
        return "?audiences=" + str(audienceid)

    def page(self, course, active_tab="tab_students", msg=None, error=None):
        """ Get all data and display the page """
        if error is None:
            error = {}
        if msg is None:
            msg = {}

        split_audiences, audiences = self.get_audiences_params(course)
        user_data = self.get_student_list_params(course)
        groups = self.user_manager.get_course_groups(course)
        student_list, audience_list, other_students, users_info = self.get_user_lists(course)

        if "csv_audiences" in web.input():
            return make_csv(audiences)
        if "csv_student" in web.input():
            return make_csv(user_data)

        return self.template_helper.get_renderer().course_admin.student_list(course, list(user_data.values()),
                                                                             split_audiences, active_tab, student_list,
                                                                             audience_list, other_students, users_info,
                                                                             groups, error, msg)

    def get_student_list_params(self, course):
        users = sorted(list(self.user_manager.get_users_info(self.user_manager.get_course_registered_users(course, False)).items()),
                       key=lambda k: k[1][0] if k[1] is not None else "")

        users = OrderedDict(sorted(list(self.user_manager.get_users_info(course.get_staff()).items()),
                                   key=lambda k: k[1][0] if k[1] is not None else "") + users)

        user_data = OrderedDict([(username, {
            "username": username, "realname": user[0] if user is not None else "",
            "email": user[1] if user is not None else "", "total_tasks": 0,
            "task_grades": {"answer": 0, "match": 0}, "task_succeeded": 0, "task_tried": 0, "total_tries": 0,
            "grade": 0, "url": self.submission_url_generator_user(username)}) for username, user in users.items()])

        for username, data in self.user_manager.get_course_caches(list(users.keys()), course).items():
            user_data[username].update(data if data is not None else {})

        return user_data

    def get_audiences_params(self, course):
        audiences = OrderedDict()
        taskids = list(course.get_tasks().keys())

        for audience in self.user_manager.get_course_audiences(course):
            audiences[audience['_id']] = dict(list(audience.items()) +
                                              [("tried", 0),
                                               ("done", 0),
                                               ("url", self.submission_url_generator_audience(audience['_id']))
                                               ])

            data = list(self.database.submissions.aggregate(
                [
                    {
                        "$match":
                            {
                                "courseid": course.get_id(),
                                "taskid": {"$in": taskids},
                                "username": {"$in": audience["students"]}
                            }
                    },
                    {
                        "$group":
                            {
                                "_id": "$taskid",
                                "tried": {"$sum": 1},
                                "done": {"$sum": {"$cond": [{"$eq": ["$result", "success"]}, 1, 0]}}
                            }
                    },

                ]))

            for c in data:
                audiences[audience['_id']]["tried"] += 1 if c["tried"] else 0
                audiences[audience['_id']]["done"] += 1 if c["done"] else 0

        my_audiences, other_audiences = [], []
        for audience in audiences.values():
            if self.user_manager.session_username() in audience["tutors"]:
                my_audiences.append(audience)
            else:
                other_audiences.append(audience)

        return [my_audiences, other_audiences], audiences

    def post_student_list(self, course, data):
        if "remove_student" in data:
            try:
                if data["type"] == "all":
                    audiences = list(self.database.audiences.find({"courseid": course.get_id()}))
                    for audience in audiences:
                        audience["students"] = []
                        self.database.audiences.replace_one({"_id": audience["_id"]}, audience)
                    groups = list(self.database.groups.find({"courseid": course.get_id()}))
                    for group in groups:
                        group["students"] = []
                        self.database.groups.replace_one({"_id": group["_id"]}, group)
                    self.database.courses.find_one_and_update({"_id": course.get_id()}, {"$set": {"students": []}})
                else:
                    self.user_manager.course_unregister_user(course, data["username"])
            except:
                pass
        elif "register_student" in data:
            try:
                self.user_manager.course_register_user(course, data["username"].strip(), '', True)
            except:
                pass

    def post_audiences(self, course, data, active_tab, msg, error):
        try:
            if 'audience' in data:
                self.database.audiences.insert({"courseid": course.get_id(), "students": [],
                                                 "tutors": [],
                                                 "description": data['audience']})
                msg["audiences"] = _("New audience created.")
                active_tab = "tab_audiences"

        except:
            msg["audiences"] = _('User returned an invalid form.')
            error["audiences"] = True
            active_tab = "tab_audiences"

        try:
            if "upload_audiences" in data or "audiences" in data:
                errored_students = []
                if "upload_audiences" in data:
                    self.database.audiences.delete_many({"courseid": course.get_id()})
                    audiences = custom_yaml.load(data["audiencefile"].file)
                else:
                    audiences = json.loads(data["audiences"])

                for index, new_audience in enumerate(audiences):
                    # In case of file upload, no id specified
                    new_audience['_id'] = new_audience['_id'] if '_id' in new_audience else 'None'

                    # Update the audience
                    audience, errors = self.update_audience(course, new_audience['_id'], new_audience)

                    # If file upload was done, get the default audience id
                    audienceid = audience['_id']
                    errored_students += errors

                if len(errored_students) > 0:
                    msg["audiences"] = _("Changes couldn't be applied for following students :") + "<ul>"
                    for student in errored_students:
                        msg["audiences"] += "<li>" + student + "</li>"
                    msg["audiences"] += "</ul>"
                    error["audiences"] = True
                elif not error:
                    msg["audiences"] = _("Audience updated.")
                active_tab = "tab_audiences"
        except Exception:
            msg["audiences"] = _('An error occurred while parsing the data.')
            error["audiences"] = True
            active_tab = "tab_audiences"
        return active_tab

    def post_groups(self, course, data, active_tab, msg, error):
        if course.is_lti():
            return active_tab

        audience_list = self.user_manager.get_course_audiences(course)
        audience_students = {}
        for audience in audience_list:
            for stud in audience["students"]:
                audience_students.setdefault(stud, []).append(audience["_id"])

        errored_students = []
        if len(data["delete"]):

            for classid in data["delete"]:
                # Get the group
                group = self.database.groups.find_one({"_id": ObjectId(classid), "courseid": course.get_id()}) if ObjectId.is_valid(classid) else None

                if group is None:
                    msg["groups"] = ("group with id {} not found.").format(classid)
                    error["groups"] = True
                else:
                    self.database.groups.find_one_and_update({"courseid": course.get_id()},
                                                                 {"$push": {
                                                                     "students": {"$each": group["students"]}
                                                                 }})

                    self.database.groups.delete_one({"_id": ObjectId(classid)})
                    msg["groups"] = _("Audience updated.")
            active_tab = "tab_groups"

        if "upload_groups" in data or "groups" in data:
            try:
                if "upload_groups" in data:
                    self.database.groups.delete_many({"courseid": course.get_id()})
                    groups = custom_yaml.load(data["groupfile"].file)
                else:
                    groups = json.loads(data["groups"])

                for index, new_group in enumerate(groups):
                    # In case of file upload, no id specified
                    new_group['_id'] = new_group['_id'] if '_id' in new_group else 'None'

                    # Update the group
                    group, errors = self.update_group(course, new_group['_id'], new_group, audience_students)
                    errored_students += errors

                if len(errored_students) > 0:
                    msg["groups"] = _("Changes couldn't be applied for following students :") + "<ul>"
                    for student in errored_students:
                        msg["groups"] += "<li>" + student + "</li>"
                    msg["groups"] += "</ul>"
                    error["groups"] = True
                elif not error:
                    msg["groups"] = _("Groups updated.")
            except:
                msg["groups"] = _('An error occurred while parsing the data.')
                error["groups"] = True
            active_tab = "tab_groups"
        return active_tab

    def get_user_lists(self, course):
        """ Get the available student list for group edition"""
        audience_list = self.user_manager.get_course_audiences(course)
        audience_list = {audience["_id"]: audience for audience in audience_list}

        student_list = self.user_manager.get_course_registered_users(course, False)
        users_info = self.user_manager.get_users_info(student_list)

        groups_list = list(self.database.groups.aggregate([
            {"$match": {"courseid": course.get_id()}},
            {"$unwind": "$students"},
            {"$project": {
                "group": "$_id",
                "students": 1
            }}
        ]))
        groups_list = {d["students"]: d["group"] for d in groups_list}

        other_students = [entry for entry in student_list if entry not in groups_list]
        other_students = sorted(other_students, key=lambda val: (("0"+users_info[val][0]) if users_info[val] else ("1"+val)))

        return student_list, audience_list, other_students, users_info

    def update_group(self, course, groupid, new_data, audience_students):
        """ Update group and returns a list of errored students"""

        student_list = self.user_manager.get_course_registered_users(course, False)

        # If group is new
        if groupid == 'None':
            # Remove _id for correct insertion
            del new_data['_id']
            new_data["courseid"] = course.get_id()

            # Insert the new group
            result = self.database.groups.insert_one(new_data)

            # Retrieve new group id
            groupid = result.inserted_id
            new_data['_id'] = result.inserted_id
            group = new_data
        else:
            group = self.database.groups.find_one({"_id": ObjectId(groupid), "courseid": course.get_id()})

        # Convert audience ids to ObjectId
        new_data["audiences"] = [ObjectId(s) for s in new_data["audiences"]]

        students, errored_students = [], []

        if len(new_data["students"]) <= new_data["size"]:
            # Check the students
            for student in new_data["students"]:
                student_allowed_in_group = any(set(audience_students.get(student, [])).intersection(new_data["audiences"]))
                if student in student_list and (student_allowed_in_group or not new_data["audiences"]):
                    # Remove user from the other group
                    self.database.groups.find_one_and_update({"courseid": course.get_id(), "students": student}, {"$pull": {"students": student}})
                    students.append(student)
                else:
                    errored_students.append(student)

        new_data["students"] = students

        group = self.database.groups.find_one_and_update(
            {"_id": ObjectId(groupid)},
            {"$set": {"description": new_data["description"], "audiences": new_data["audiences"], "size": new_data["size"],
                      "students": students}}, return_document=ReturnDocument.AFTER)

        return group, errored_students

    def update_audience(self, course, audienceid, new_data):
        """ Update audience and returns a list of errored students"""

        student_list = self.user_manager.get_course_registered_users(course, False)

        # If audience is new
        if audienceid == 'None':
            # Remove _id for correct insertion
            del new_data['_id']
            new_data["courseid"] = course.get_id()

            # Insert the new audience
            result = self.database.audiences.insert_one(new_data)

            # Retrieve new audience id
            audienceid = result.inserted_id
            new_data['_id'] = result.inserted_id
            audience = new_data
        else:
            audience = self.database.audiences.find_one({"_id": ObjectId(audienceid), "courseid": course.get_id()})

        # Check tutors
        new_data["tutors"] = [tutor for tutor in new_data["tutors"] if tutor in course.get_staff()]

        students, errored_students = [], []

        # Check the students
        for student in new_data["students"]:
            if student in student_list:
                students.append(student)
            else:
                # Check if user can be registered
                user_info = self.user_manager.get_user_info(student)
                if user_info is None or student in audience["tutors"]:
                    errored_students.append(student)
                else:
                    self.user_manager.course_register_user(course, student, force=True)
                    students.append(student)

        removed_students = [student for student in audience["students"] if student not in new_data["students"]]
        self.database.audiences.find_one_and_update({"courseid": course.get_id()},
                                                     {"$push": {"students": {"$each": removed_students}}})

        new_data["students"] = students

        audience = self.database.audiences.find_one_and_update(
            {"_id": ObjectId(audienceid)},
            {"$set": {"description": new_data["description"],
                      "students": students, "tutors": new_data["tutors"]}}, return_document=ReturnDocument.AFTER)

        return audience, errored_students