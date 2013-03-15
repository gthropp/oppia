# Copyright 2012 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Common utility functions."""

__author__ = 'sll@google.com (Sean Lip)'

import base64
import copy
import hashlib
import json
import logging
import os

from jinja2 import Environment
from jinja2 import meta

import feconf
from models.exploration import Exploration
from models.models import AugmentedUser
from models.models import Widget
from models.state import State

from google.appengine.ext import ndb


END_DEST = 'END'


class InvalidInputException(Exception):
    """Error class for invalid input."""
    pass


class EntityIdNotFoundError(Exception):
    """Error class for when an entity ID is not in the datastore."""
    pass


def create_enum(*sequential, **names):
    enums = dict(zip(sequential, sequential), **names)
    return type('Enum', (), enums)


def log(message):
    """Logs info messages in development/debug mode.

    Args:
        message: the message to be logged.
    """
    if feconf.DEV or feconf.DEBUG:
        if isinstance(message, dict):
            logging.info(json.dumps(message, sort_keys=True, indent=4))
        else:
            logging.info(str(message))


def check_existence_of_name(entity, name, ancestor=None):
    """Checks whether an entity with the given name and ancestor already exists.

    Args:
        entity: the name of the entity's class.
        name: string representing the entity name.
        ancestor: the ancestor entity, if applicable.

    Returns:
        True if an entity exists with the same name and ancestor, else False.

    Raises:
        EntityIdNotFoundError: If no entity name is supplied.
        KeyError: If a state entity is queried and no ancestor is supplied.
    """
    entity_type = entity.__name__.lower()
    if not name:
        raise EntityIdNotFoundError('No %s name supplied', entity_type)
    if ancestor:
        entity = entity.query(ancestor=ancestor.key).filter(
            entity.name == name).get()
    else:
        if entity == State:
            raise KeyError('Queries for state entities must include ancestors.')
        else:
            entity = entity.query().filter(entity.name == name).get()
    if not entity:
        return False
    return True


def get_state_by_name(name, exploration):
    """Gets the state with this name in this exploration.

    Args:
        name: string representing the entity name.
        exploration: the exploration to which this state belongs

    Returns:
        the state, if it exists; None otherwise.

    Raises:
        EntityIdNotFoundError: if the state name is not provided.
        KeyError: if no exploration is given.
    """
    if not name:
        raise EntityIdNotFoundError('No state name supplied')
    if not exploration:
        raise KeyError('Queries for state entities must include explorations.')
    return State.query(ancestor=exploration.key).filter(
        State.name == name).get()


def check_can_edit(user, exploration):
    """Checks whether the current user has rights to edit this exploration."""
    return (user.email() in exploration.editors or
            exploration.key in get_augmented_user(user).editable_explorations)


def get_new_id(entity, entity_name):
    """Gets a new id for an entity, based on its name.

    Args:
        entity: the name of the entity's class.
        entity_name: string representing the name of the entity

    Returns:
        string - the id representing the entity
    """
    new_id = base64.urlsafe_b64encode(
        hashlib.sha1(entity_name.encode('utf-8')).digest())[:10]
    seed = 0
    while entity.get_by_id(new_id):
        seed += 1
        new_id = base64.urlsafe_b64encode(
            hashlib.sha1('%s%s' % (
                entity_name.encode('utf-8'), seed)).digest())[:10]
    return new_id


def get_file_contents(root, filepath):
    """Gets the contents of a file.

    Args:
        root: the path to prepend to the filepath.
        filepath: a path to a HTML, JS or CSS file. It should not include the
            template/dev/head or template/prod/head prefix.

    Returns:
        the file contents.
    """
    with open(os.path.join(root, filepath)) as f:
        return f.read().decode('utf-8')


def get_js_controllers(filenames):
    """Gets the concatenated contents of some JS controllers.

    Args:
        filenames: an array with names of JS files (without the '.js' suffix).

    Returns:
        the concatenated contents of these JS files.
    """
    return '\n'.join([
        get_file_contents(
            feconf.TEMPLATE_DIR, 'js/controllers/%s.js' % filename
        ) for filename in filenames
    ])


def parse_content_into_html(content_array, block_number, params=None):
    """Takes a Content array and transforms it into HTML.

    Args:
        content_array: an array, each of whose members is of type Content. This
            object has two keys: type and value. The 'type' is one of the
            following:
                - 'text'; then the value is a text string
                - 'image'; then the value is an image ID
                - 'video'; then the value is a video ID
                - 'widget'; then the value is a widget ID
        block_number: the number of content blocks preceding this one.
        params: any parameters used for templatizing text strings.

    Returns:
        the HTML string representing the array.

    Raises:
        InvalidInputException: if content has no 'type' attribute, or an invalid
            'type' attribute.
    """
    if params is None:
        params = {}

    html = ''
    widget_array = []
    widget_counter = 0
    for content in content_array:
        if content.type == 'widget':
            try:
                widget = Widget.get(content.value)
                widget_counter += 1
                html += feconf.JINJA_ENV.get_template('content.html').render({
                    'type': content.type, 'blockIndex': block_number,
                    'index': widget_counter})
                widget_array.append({
                    'blockIndex': block_number,
                    'index': widget_counter,
                    'code': widget.raw})
            except EntityIdNotFoundError:
                # Ignore empty widget content.
                pass
        elif (content.type in ['text', 'image', 'video']):
            if content.type == 'text':
                value = parse_with_jinja(content.value, params)
            else:
                value = content.value

            html += feconf.JINJA_ENV.get_template('content.html').render({
                'type': content.type, 'value': value})
        else:
            raise InvalidInputException(
                'Invalid content type %s', content.type)
    return html, widget_array


def get_augmented_user(user):
    """Gets (or creates) the corresponding AugmentedUser."""
    augmented_user = AugmentedUser.query().filter(
        AugmentedUser.user == user).get()
    if not augmented_user:
        augmented_user = AugmentedUser(user=user)
        augmented_user.put()
    return augmented_user


def create_new_exploration(
    user, title='New Exploration', category='No category', exploration_id=None,
    init_state_name='Activity 1'):
    """Creates and returns a new exploration."""
    if exploration_id is None:
        exploration_id = get_new_id(Exploration, title)
    state_id = get_new_id(State, init_state_name)

    # Create a fake state key temporarily for initialization of the question.
    # TODO(sll): Do this in a transaction so it doesn't break other things.
    fake_state_key = ndb.Key(State, state_id)

    exploration = Exploration(
        id=exploration_id, init_state=fake_state_key,
        owner=user, category=category)
    if title:
        exploration.title = title
    exploration.put()
    new_init_state = State.create(state_id, exploration, init_state_name)

    # Replace the fake key with its real counterpart.
    exploration.init_state = new_init_state.key
    exploration.states = [new_init_state.key]
    exploration.put()
    if user:
        augmented_user = get_augmented_user(user)
        augmented_user.editable_explorations.append(exploration.key)
        augmented_user.put()
    return exploration


def create_new_state(exploration, state_name):
    """Creates and returns a new state."""
    state_id = get_new_id(State, state_name)
    state = State.create(state_id, exploration, state_name)

    exploration.states.append(state.key)
    exploration.put()
    return state


def delete_exploration(exploration):
    """Deletes an exploration."""
    augmented_users = AugmentedUser.query().filter(
        AugmentedUser.editable_explorations == exploration.key)
    for augmented_user in augmented_users:
        augmented_user.editable_explorations.remove(exploration.key)
        augmented_user.put()

    exploration.delete()


def parse_with_jinja(string, params, default=''):
    """Parses a string using Jinja templating.

    Args:
      string: the string to be parsed.
      params: the parameters to parse the string with.
      default: the default string to use for missing parameters.

    Returns:
      the parsed string, or None if the string could not be parsed.
    """
    variables = meta.find_undeclared_variables(
        Environment().parse(string))

    new_params = copy.deepcopy(params)
    for var in variables:
        if var not in new_params:
            new_params[var] = default
            logging.info('Cannot parse %s properly using %s', string, params)

    return Environment().from_string(string).render(new_params)


def get_comma_sep_string_from_list(items):
    """Turns a list of items into a comma-separated string."""

    if not items:
        return ''

    if len(items) == 1:
        return items[0]

    return '%s and %s' % (', '.join(items[:-1]), items[-1])


def is_demo_exploration(exploration_id):
    """Checks if the exploration is one of the demos."""

    return len(exploration_id) < 4


def encode_strings_as_ascii(obj):
    """Recursively tries to encode strings in an object as ASCII strings."""
    if isinstance(obj, int) or isinstance(obj, set):
        return obj
    elif isinstance(obj, str) or isinstance(obj, unicode):
        return str(obj)
    elif isinstance(obj, list):
        return [encode_strings_as_ascii(item) for item in obj]
    elif isinstance(obj, dict):
        new_dict = {}
        for item in obj:
            new_dict[encode_strings_as_ascii(item)] = (
                encode_strings_as_ascii(obj[item]))
    else:
        return obj


def to_string(string):
    """Removes unicode characters from a string."""
    return string.encode('ascii', 'ignore')
